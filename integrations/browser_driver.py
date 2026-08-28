"""Driver Playwright éphémère du navigateur agentique sécurisé."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from integrations.browser_security import (
    BrowserNetworkPolicy,
    BrowserRequestGuard,
    BrowserSecurityError,
    PublicHTTPSNetworkPolicy,
    SecureEgressProxy,
)
from integrations.playwright_runtime import import_playwright

MAX_ELEMENTS = 60
MAX_SCAN_ELEMENTS = 240
MAX_TEXT_CHARS = 4_000
MAX_NAME_CHARS = 80
_INTERACTIVE = (
    "a, button, input, select, textarea, "
    "[role='button'], [role='link'], [role='textbox'], "
    "[role='combobox'], [role='searchbox']"
)
_ELEMENT_METADATA = r"""
el => {
  const form = el.form || el.closest?.('form') || null;
  const rawTag = String(el.tagName || '').toLowerCase();
  const rawRole = String(el.getAttribute?.('role') || rawTag).toLowerCase();
  const rawText = String(el.innerText || el.textContent || '')
    .replace(/\s+/g, ' ').trim();
  const rawName = String(
    el.getAttribute?.('aria-label') ||
    el.getAttribute?.('placeholder') ||
    rawText
  ).replace(/\s+/g, ' ').trim();
  const rawType = String(el.type || el.getAttribute?.('type') || '').toLowerCase();
  const rawHref = typeof el.href === 'string' ? el.href : '';
  const isSearchInput = rawTag === 'input' &&
    (rawType === 'search' || rawRole === 'searchbox');
  const ownFormAction = isSearchInput ? null : el.getAttribute?.('formaction');
  const rawFormAction = String(
    ownFormAction !== null && ownFormAction !== undefined
      ? new URL(ownFormAction, document.baseURI).href
      : form?.action || ''
  );
  const rawFormMethod = String(
    isSearchInput ? form?.method || 'get' : el.formMethod || form?.method || 'get'
  ).toLowerCase();
  const rawAutocomplete = String(
    el.autocomplete || el.getAttribute?.('autocomplete') || ''
  ).toLowerCase();
  const formHasPassword = Boolean(
    Array.from(form?.elements || []).some(
      item => String(item.type || '').toLowerCase() === 'password'
    )
  );
  const autocompleteTokens = rawAutocomplete.split(/\s+/).filter(Boolean);
  const sensitiveAutocomplete = autocompleteTokens.some(
    token => token.startsWith('cc-') || [
      'current-password', 'new-password', 'one-time-code', 'username',
      'webauthn', 'transaction-amount', 'transaction-currency'
    ].includes(token)
  );
  const rawTarget = String(
    isSearchInput ? form?.target || '' : el.target || form?.target || ''
  ).toLowerCase();
  const rawFieldName = String(el.name || el.getAttribute?.('name') || '');
  const rawValue = isSearchInput && !formHasPassword && !sensitiveAutocomplete &&
    typeof el.value === 'string' ? el.value : '';
  const bounded = (value, limit) => String(value).slice(0, limit);
  const oversized = [
    [rawTag, 32], [rawRole, 40], [rawName, 80], [rawType, 32],
    [rawHref, 2000], [rawFormAction, 2000], [rawFormMethod, 16],
    [rawAutocomplete, 80], [rawTarget, 32], [rawFieldName, 160],
    [rawValue, 500]
  ].some(([value, limit]) => value.length > limit);
  return {
    tag: bounded(rawTag, 32),
    role: bounded(rawRole, 40),
    name: bounded(rawName, 80),
    type: bounded(rawType, 32),
    href: bounded(rawHref, 2000),
    form_action: bounded(rawFormAction, 2000),
    form_method: bounded(rawFormMethod, 16),
    autocomplete: bounded(rawAutocomplete, 80),
    target: bounded(rawTarget, 32),
    download: Boolean(el.hasAttribute?.('download')),
    contenteditable: Boolean(el.isContentEditable),
    form_has_password: formHasPassword,
    field_name: bounded(rawFieldName, 160),
    disabled: Boolean(el.disabled),
    readonly: Boolean(el.readOnly),
    inline_handler: Boolean(el.getAttribute?.('onclick')),
    value: bounded(rawValue, 500),
    oversized
  };
}
"""


def _clean(value: Any, *, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())[:limit]


def _fingerprint(values: dict[str, Any]) -> str:
    identity = {
        key: values[key]
        for key in (
            "autocomplete",
            "contenteditable",
            "disabled",
            "download",
            "field_name",
            "form_action",
            "form_has_password",
            "form_method",
            "href",
            "inline_handler",
            "name",
            "readonly",
            "role",
            "tag",
            "target",
            "type",
            "value_digest",
        )
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class BrowserElement:
    ref: str
    role: str
    name: str
    index: int
    tag: str = ""
    input_type: str = ""
    href: str = ""
    form_action: str = ""
    form_method: str = ""
    autocomplete: str = ""
    target: str = ""
    download: bool = False
    contenteditable: bool = False
    form_has_password: bool = False
    field_name: str = ""
    disabled: bool = False
    readonly: bool = False
    inline_handler: bool = False
    fingerprint: str = ""
    generation: int = 0
    snapshot_id: str = ""
    page_url: str = ""
    handle: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class LiveBrowserElement:
    element: BrowserElement
    value: str = field(default="", repr=False, compare=False)


def _from_metadata(
    raw: Any,
    *,
    ref: str,
    index: int,
    handle: Any,
    generation: int = 0,
    snapshot_id: str = "",
    page_url: str = "",
) -> LiveBrowserElement:
    if not isinstance(raw, dict):
        raise BrowserSecurityError("dom_metadata_invalid", "Métadonnées DOM invalides")
    if raw.get("oversized") is True:
        raise BrowserSecurityError(
            "dom_metadata_oversized", "Métadonnées DOM trop volumineuses"
        )
    values: dict[str, Any] = {
        "tag": _clean(raw.get("tag"), limit=32).lower(),
        "role": _clean(raw.get("role"), limit=40).lower(),
        "name": _clean(raw.get("name"), limit=MAX_NAME_CHARS),
        "type": _clean(raw.get("type"), limit=32).lower(),
        "href": str(raw.get("href") or "")[:2_000],
        "form_action": str(raw.get("form_action") or "")[:2_000],
        "form_method": _clean(raw.get("form_method"), limit=16).lower(),
        "autocomplete": _clean(raw.get("autocomplete"), limit=80).lower(),
        "target": _clean(raw.get("target"), limit=32).lower(),
        "download": raw.get("download") is True,
        "contenteditable": raw.get("contenteditable") is True,
        "form_has_password": raw.get("form_has_password") is True,
        "field_name": _clean(raw.get("field_name"), limit=160),
        "disabled": raw.get("disabled") is True,
        "readonly": raw.get("readonly") is True,
        "inline_handler": raw.get("inline_handler") is True,
        "value_digest": hashlib.sha256(
            str(raw.get("value") or "").encode()
        ).hexdigest(),
    }
    return LiveBrowserElement(
        element=BrowserElement(
            ref=ref,
            role=values["role"],
            name=values["name"],
            index=index,
            tag=values["tag"],
            input_type=values["type"],
            href=values["href"],
            form_action=values["form_action"],
            form_method=values["form_method"],
            autocomplete=values["autocomplete"],
            target=values["target"],
            download=values["download"],
            contenteditable=values["contenteditable"],
            form_has_password=values["form_has_password"],
            field_name=values["field_name"],
            disabled=values["disabled"],
            readonly=values["readonly"],
            inline_handler=values["inline_handler"],
            fingerprint=_fingerprint(values),
            generation=generation,
            snapshot_id=snapshot_id,
            page_url=page_url,
            handle=handle,
        ),
        value=str(raw.get("value") or "")[:500],
    )


class PlaywrightDriver:
    """Un Chromium, un contexte et un proxy réseau par run."""

    def __init__(
        self,
        *,
        network_policy: BrowserNetworkPolicy | None = None,
        ignore_https_errors: bool = False,
    ) -> None:
        self.url = ""
        self._policy = network_policy or PublicHTTPSNetworkPolicy()
        self._ignore_https_errors = ignore_https_errors
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._guard = BrowserRequestGuard(self._policy)
        self._proxy = SecureEgressProxy(self._policy)
        self._event_error: BrowserSecurityError | None = None

    async def start(self, *, headless: bool, nav_ms: int, act_ms: int) -> None:
        try:
            await self._proxy.start()
            api = import_playwright()
            self._playwright = await api.async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=headless,
                proxy={"server": self._proxy.server_url, "bypass": ""},
                args=[
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-quic",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                    "--proxy-bypass-list=<-loopback>",
                ],
            )
            self._context = await self._browser.new_context(
                accept_downloads=False,
                ignore_https_errors=self._ignore_https_errors,
                java_script_enabled=False,
                service_workers="block",
            )
            await self._context.clear_permissions()
            await self._guard.install(self._context)
            self._page = await self._context.new_page()
            self._guard.bind_page(self._page)
            self._page.set_default_timeout(act_ms)
            self._page.set_default_navigation_timeout(nav_ms)
            self._page.on("download", self._reject_download)
            self._page.on("filechooser", self._reject_filechooser)
            self._context.on("page", self._reject_extra_page)
        except BaseException:
            cleanup = asyncio.create_task(self.close())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
            raise

    async def _reject_download(self, download: Any) -> None:
        self._event_error = BrowserSecurityError(
            "download_blocked", "Téléchargement interdit"
        )
        await download.cancel()

    def _reject_filechooser(self, _chooser: Any) -> None:
        self._event_error = BrowserSecurityError(
            "file_upload_blocked", "Sélecteur de fichier interdit"
        )

    async def _reject_extra_page(self, page: Any) -> None:
        if page is self._page:
            return
        self._event_error = BrowserSecurityError("popup_blocked", "Popup interdite")
        await page.close()

    def _raise_if_blocked(self) -> None:
        self._proxy.raise_if_blocked()
        self._guard.raise_if_blocked()
        if self._event_error is not None:
            raise self._event_error

    async def _validate_page(self) -> None:
        self._raise_if_blocked()
        await self._guard.validate_page(self._page)
        self._raise_if_blocked()

    async def open(self, url: str) -> None:
        endpoint = await asyncio.to_thread(self._policy.resolve, url)
        self._guard.authorize_document(endpoint.url)
        try:
            await self._page.goto(endpoint.url, wait_until="domcontentloaded")
        except Exception:
            self._raise_if_blocked()
            raise
        finally:
            self._guard.clear_document_authorization()
        await self._validate_page()
        self.url = str(self._page.url or endpoint.url)

    async def observe(self) -> tuple[str, str, str, list[BrowserElement]]:
        await self._validate_page()
        page = self._page
        self.url = str(page.url or self.url)
        title = str(
            await page.evaluate("limit => String(document.title || '').slice(0, limit)", 200)
            or ""
        )
        try:
            text = str(
                await page.locator("body").evaluate(
                    "(el, limit) => String(el.innerText || '').slice(0, limit)",
                    MAX_TEXT_CHARS,
                )
                or ""
            )
        except Exception:
            text = ""
        locator = page.locator(_INTERACTIVE)
        scan_count = min(int(await locator.count()), MAX_SCAN_ELEMENTS)
        elements: list[BrowserElement] = []
        for index in range(scan_count):
            item = locator.nth(index)
            try:
                if not await item.is_visible():
                    continue
                handle = await item.element_handle()
                if handle is None:
                    continue
                raw = await handle.evaluate(_ELEMENT_METADATA)
            except Exception:
                continue
            live = _from_metadata(
                raw,
                ref="",
                index=index,
                handle=handle,
                page_url=self.url,
            )
            elements.append(live.element)
            if len(elements) >= MAX_ELEMENTS:
                break
        await self._validate_page()
        return self.url, title, text, elements

    async def inspect(self, element: BrowserElement) -> LiveBrowserElement:
        await self._validate_page()
        live_url = str(self._page.url or "")
        if (
            self.url != element.page_url
            or live_url != element.page_url
            or element.handle is None
        ):
            raise BrowserSecurityError("stale_ref", "Référence DOM périmée")
        try:
            connected = await element.handle.evaluate("el => document.contains(el)")
            if not connected:
                raise BrowserSecurityError("stale_ref", "Référence DOM périmée")
            raw = await element.handle.evaluate(_ELEMENT_METADATA)
        except BrowserSecurityError:
            raise
        except Exception as exc:
            raise BrowserSecurityError("stale_ref", "Référence DOM périmée") from exc
        live = _from_metadata(
            raw,
            ref=element.ref,
            index=element.index,
            handle=element.handle,
            generation=element.generation,
            snapshot_id=element.snapshot_id,
            page_url=element.page_url,
        )
        if live.element.fingerprint != element.fingerprint:
            raise BrowserSecurityError("stale_ref", "Identité DOM modifiée")
        return live

    async def submit_search(self, live: LiveBrowserElement, text: str) -> None:
        parsed = urlsplit(live.element.form_action)
        query = urlencode({live.element.field_name: text})
        target = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))
        await self.open(target)

    async def close(self) -> None:
        first_error: Exception | None = None
        for handle in (self._page, self._context, self._browser):
            if handle is None:
                continue
            try:
                await asyncio.wait_for(handle.close(), timeout=3.0)
            except Exception as exc:
                first_error = first_error or exc
        if self._playwright is not None:
            try:
                await asyncio.wait_for(self._playwright.stop(), timeout=3.0)
            except Exception as exc:
                first_error = first_error or exc
        try:
            await asyncio.wait_for(self._proxy.close(), timeout=3.0)
        except Exception as exc:
            first_error = first_error or exc
        self._page = self._context = self._browser = self._playwright = None
        self.url = ""
        if first_error is not None:
            raise first_error


__all__ = ["BrowserElement", "LiveBrowserElement", "PlaywrightDriver"]
