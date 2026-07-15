package fr.jarvis.companion;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.GeolocationPermissions;
import android.webkit.PermissionRequest;
import android.webkit.SslErrorHandler;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.ArrayList;
import java.util.List;

/** Première enveloppe Android privée de JARVIS. */
public final class MainActivity extends Activity {
    private static final String PREFS = "jarvis_android";
    private static final String PREF_SERVER = "server_url";
    private static final String DEFAULT_SERVER = "https://100.123.50.38:8081";
    private static final int REQ_MIC = 1001;
    private static final int REQ_GEO = 1002;
    private static final int REQ_FILE = 1003;

    private SharedPreferences preferences;
    private WebView webView;
    private ProgressBar progress;
    private LinearLayout offlinePanel;
    private TextView offlineDetail;
    private String serverUrl;
    private PermissionRequest pendingPermission;
    private GeolocationPermissions.Callback geoCallback;
    private String geoOrigin;
    private ValueCallback<Uri[]> fileCallback;
    private boolean sslDialogVisible;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        preferences = getSharedPreferences(PREFS, MODE_PRIVATE);
        serverUrl = preferences.getString(PREF_SERVER, DEFAULT_SERVER);
        getWindow().setStatusBarColor(Color.rgb(10, 10, 15));
        getWindow().setNavigationBarColor(Color.rgb(10, 10, 15));
        buildUi();
        configureWebView();
        if (preferences.contains(PREF_SERVER)) loadJarvis();
        else showServerDialog(false);
    }

    private void buildUi() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(10, 10, 15));

        webView = new WebView(this);
        root.addView(webView, matchFrame());

        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setIndeterminate(true);
        FrameLayout.LayoutParams progressParams = new FrameLayout.LayoutParams(-1, dp(3));
        progressParams.gravity = Gravity.TOP;
        root.addView(progress, progressParams);

        offlinePanel = new LinearLayout(this);
        offlinePanel.setOrientation(LinearLayout.VERTICAL);
        offlinePanel.setGravity(Gravity.CENTER);
        offlinePanel.setPadding(dp(32), dp(32), dp(32), dp(32));
        offlinePanel.setBackgroundColor(Color.rgb(10, 10, 15));
        offlinePanel.setVisibility(View.GONE);

        TextView title = text("JARVIS est injoignable", 22, Color.WHITE);
        offlinePanel.addView(title, linearParams(dp(12)));
        offlineDetail = text("", 14, Color.rgb(150, 150, 160));
        offlinePanel.addView(offlineDetail, linearParams(dp(22)));

        Button retry = button("Réessayer");
        retry.setOnClickListener(v -> loadJarvis());
        offlinePanel.addView(retry, linearParams(dp(10)));
        Button change = button("Adresse du serveur");
        change.setOnClickListener(v -> showServerDialog(true));
        offlinePanel.addView(change, linearParams(0));
        root.addView(offlinePanel, matchFrame());

        Button menu = new Button(this);
        menu.setText("⋮");
        menu.setTextColor(Color.WHITE);
        menu.setTextSize(22);
        menu.setMinWidth(0);
        menu.setMinimumWidth(0);
        menu.setMinHeight(0);
        menu.setMinimumHeight(0);
        menu.setPadding(0, 0, 0, dp(5));
        menu.setAlpha(0.82f);
        menu.setContentDescription("Changer l'adresse du serveur JARVIS");
        menu.setOnClickListener(v -> showServerDialog(true));
        FrameLayout.LayoutParams menuParams = new FrameLayout.LayoutParams(dp(42), dp(42));
        menuParams.gravity = Gravity.TOP | Gravity.END;
        menuParams.setMargins(dp(8), dp(8), dp(8), 0);
        root.addView(menu, menuParams);
        setContentView(root);
    }

    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setGeolocationEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setUserAgentString(settings.getUserAgentString() + " JARVIS-Android/0.1.0");
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, false);
        WebView.setWebContentsDebuggingEnabled(false);
        webView.setWebViewClient(new JarvisWebClient());
        webView.setWebChromeClient(new JarvisChromeClient());
        webView.setDownloadListener((url, ua, disposition, type, length) ->
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url))));
    }

    private void showServerDialog(boolean cancelable) {
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setText(serverUrl);
        input.setSelectAllOnFocus(true);
        input.setPadding(dp(18), dp(12), dp(18), dp(12));
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("Connexion à JARVIS")
                .setMessage("Adresse Tailscale HTTPS du Mac. Le S24 et le Mac doivent être connectés à Tailscale.")
                .setView(input)
                .setCancelable(cancelable)
                .setNegativeButton(cancelable ? "Annuler" : "Quitter", (d, which) -> {
                    if (!cancelable) finish();
                })
                .setPositiveButton("Connecter", null)
                .create();
        dialog.setOnShowListener(unused -> dialog.getButton(AlertDialog.BUTTON_POSITIVE)
                .setOnClickListener(v -> {
                    String normalized = normalizeUrl(input.getText().toString());
                    if (normalized == null) {
                        input.setError("Adresse HTTP(S) invalide");
                        return;
                    }
                    serverUrl = normalized;
                    preferences.edit().putString(PREF_SERVER, serverUrl).apply();
                    dialog.dismiss();
                    loadJarvis();
                }));
        dialog.show();
    }

    private String normalizeUrl(String raw) {
        String value = raw == null ? "" : raw.trim();
        if (value.isEmpty()) return null;
        if (!value.startsWith("http://") && !value.startsWith("https://")) value = "https://" + value;
        try {
            URI uri = new URI(value);
            if (uri.getHost() == null) return null;
            if (!"http".equalsIgnoreCase(uri.getScheme()) && !"https".equalsIgnoreCase(uri.getScheme())) return null;
            return uri.getScheme().toLowerCase() + "://" + uri.getRawAuthority();
        } catch (URISyntaxException ignored) {
            return null;
        }
    }

    private void loadJarvis() {
        if (!isOnline()) {
            showOffline("Aucun réseau détecté. Vérifie la 4G/5G, le Wi-Fi et Tailscale.");
            return;
        }
        offlinePanel.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
        progress.setVisibility(View.VISIBLE);
        webView.loadUrl(serverUrl + "/chat?jarvis_android=1");
    }

    private boolean isOnline() {
        ConnectivityManager manager = (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
        if (manager == null || manager.getActiveNetwork() == null) return false;
        NetworkCapabilities caps = manager.getNetworkCapabilities(manager.getActiveNetwork());
        return caps != null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET);
    }

    private void showOffline(String message) {
        progress.setVisibility(View.GONE);
        webView.setVisibility(View.GONE);
        offlineDetail.setText(message + "\n\nServeur : " + serverUrl);
        offlinePanel.setVisibility(View.VISIBLE);
    }

    private boolean isJarvisUri(Uri uri) {
        try {
            URI configured = new URI(serverUrl);
            return configured.getHost() != null
                    && configured.getHost().equalsIgnoreCase(uri.getHost())
                    && configured.getPort() == uri.getPort();
        } catch (URISyntaxException ignored) {
            return false;
        }
    }

    private final class JarvisWebClient extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            if (isJarvisUri(request.getUrl())) return false;
            startActivity(new Intent(Intent.ACTION_VIEW, request.getUrl()));
            return true;
        }

        @Override public void onPageStarted(WebView view, String url, android.graphics.Bitmap icon) {
            progress.setVisibility(View.VISIBLE);
        }

        @Override public void onPageFinished(WebView view, String url) {
            progress.setVisibility(View.GONE);
            webView.setVisibility(View.VISIBLE);
            offlinePanel.setVisibility(View.GONE);
        }

        @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (request.isForMainFrame()) showOffline("Connexion au Mac impossible. Vérifie JARVIS et Tailscale.");
        }

        @Override public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
            if (sslDialogVisible) {
                handler.cancel();
                return;
            }
            sslDialogVisible = true;
            String host = error.getUrl() == null ? "serveur inconnu" : Uri.parse(error.getUrl()).getHost();
            new AlertDialog.Builder(MainActivity.this)
                    .setTitle("Certificat JARVIS non reconnu")
                    .setMessage("Vérifie que " + host + " est bien ton Mac sur Tailscale. L’acceptation ne vaut que pour cette session.")
                    .setNegativeButton("Refuser", (d, which) -> handler.cancel())
                    .setPositiveButton("Accepter cette session", (d, which) -> handler.proceed())
                    .setOnDismissListener(d -> sslDialogVisible = false)
                    .show();
        }
    }

    private final class JarvisChromeClient extends WebChromeClient {
        @Override public void onPermissionRequest(PermissionRequest request) {
            runOnUiThread(() -> handleWebPermission(request));
        }

        @Override public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) {
            if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED) {
                callback.invoke(origin, true, false);
            } else {
                geoOrigin = origin;
                geoCallback = callback;
                requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION}, REQ_GEO);
            }
        }

        @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
            if (fileCallback != null) fileCallback.onReceiveValue(null);
            fileCallback = callback;
            try {
                startActivityForResult(params.createIntent(), REQ_FILE);
                return true;
            } catch (Exception ignored) {
                fileCallback = null;
                return false;
            }
        }
    }

    private void handleWebPermission(PermissionRequest request) {
        List<String> allowed = new ArrayList<>();
        boolean audioRequested = false;
        for (String resource : request.getResources()) {
            if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(resource)) {
                audioRequested = true;
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) allowed.add(resource);
            }
        }
        if (audioRequested && allowed.isEmpty()) {
            pendingPermission = request;
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_MIC);
        } else if (!allowed.isEmpty()) request.grant(allowed.toArray(new String[0]));
        else request.deny();
    }

    @Override public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
        boolean granted = results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED;
        if (code == REQ_MIC && pendingPermission != null) {
            PermissionRequest request = pendingPermission;
            pendingPermission = null;
            if (granted) request.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});
            else request.deny();
        } else if (code == REQ_GEO && geoCallback != null) {
            geoCallback.invoke(geoOrigin, granted, false);
            geoCallback = null;
            geoOrigin = null;
        }
    }

    @Override protected void onActivityResult(int code, int resultCode, Intent data) {
        super.onActivityResult(code, resultCode, data);
        if (code == REQ_FILE && fileCallback != null) {
            fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data));
            fileCallback = null;
        }
    }

    @Override public void onBackPressed() {
        if (offlinePanel.getVisibility() != View.VISIBLE && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override protected void onDestroy() {
        webView.stopLoading();
        webView.destroy();
        super.onDestroy();
    }

    private FrameLayout.LayoutParams matchFrame() { return new FrameLayout.LayoutParams(-1, -1); }
    private LinearLayout.LayoutParams linearParams(int bottom) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.bottomMargin = bottom;
        return params;
    }
    private TextView text(String value, int size, int color) {
        TextView text = new TextView(this);
        text.setText(value);
        text.setTextSize(size);
        text.setTextColor(color);
        text.setGravity(Gravity.CENTER);
        return text;
    }
    private Button button(String label) {
        Button button = new Button(this);
        button.setText(label);
        button.setTextColor(Color.WHITE);
        button.setAllCaps(false);
        button.setMinWidth(dp(220));
        return button;
    }
    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
}
