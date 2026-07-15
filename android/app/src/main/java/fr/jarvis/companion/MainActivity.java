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
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
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

import com.google.firebase.FirebaseApp;
import com.google.firebase.messaging.FirebaseMessaging;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.ArrayList;
import java.util.List;

/** Compagnon Android privé de JARVIS, appairé au Mac par jeton natif. */
public final class MainActivity extends Activity {
    private static final int REQ_WEB_MIC = 1001;
    private static final int REQ_WEB_GEO = 1002;
    private static final int REQ_FILE = 1003;
    private static final int REQ_LOCATION_FOREGROUND = 1004;
    private static final int REQ_LOCATION_BACKGROUND = 1005;
    private static final int REQ_WAKE_MIC = 1006;
    private static final int REQ_NOTIFICATIONS = 1007;

    private SharedPreferences preferences;
    private WebView webView;
    private ProgressBar progress;
    private LinearLayout offlinePanel;
    private TextView offlineDetail;
    private String serverUrl;
    private PermissionRequest pendingWebPermission;
    private GeolocationPermissions.Callback geoCallback;
    private String geoOrigin;
    private ValueCallback<Uri[]> fileCallback;
    private boolean locationSettingsPending;
    private boolean openVoice;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        preferences = JarvisSettings.preferences(this);
        serverUrl = JarvisSettings.server(this);
        openVoice = getIntent().getBooleanExtra("open_voice", false);
        getWindow().setStatusBarColor(Color.rgb(10, 10, 15));
        getWindow().setNavigationBarColor(Color.rgb(10, 10, 15));
        JarvisNotifications.createChannels(this);
        buildUi();
        configureWebView();
        requestNotificationPermission();
        if (preferences.contains(JarvisSettings.PREF_SERVER)) authenticateNativeOrPair();
        else showServerDialog(false);
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        if (intent.getBooleanExtra("open_voice", false)) {
            openVoice = true;
            if (webView != null) webView.loadUrl(serverUrl + "/voice?jarvis_android=1");
        }
    }

    private void buildUi() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(10, 10, 15));
        webView = new WebView(this);
        root.addView(webView, new FrameLayout.LayoutParams(-1, -1));

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
        offlinePanel.addView(text("JARVIS est injoignable", 22, Color.WHITE), linearParams(dp(12)));
        offlineDetail = text("", 14, Color.rgb(150, 150, 160));
        offlinePanel.addView(offlineDetail, linearParams(dp(22)));
        Button retry = button("Réessayer");
        retry.setOnClickListener(v -> authenticateNativeOrPair());
        offlinePanel.addView(retry, linearParams(dp(10)));
        Button settings = button("Réglages JARVIS");
        settings.setOnClickListener(v -> showSettings());
        offlinePanel.addView(settings, linearParams(0));
        root.addView(offlinePanel, new FrameLayout.LayoutParams(-1, -1));

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
        menu.setContentDescription("Réglages JARVIS");
        menu.setOnClickListener(v -> showSettings());
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
        settings.setUserAgentString(settings.getUserAgentString()
                + " JARVIS-Android/" + BuildConfig.VERSION_NAME);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, false);
        WebView.setWebContentsDebuggingEnabled(false);
        webView.setWebViewClient(new JarvisWebClient());
        webView.setWebChromeClient(new JarvisChromeClient());
        webView.setDownloadListener((url, ua, disposition, type, length) ->
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url))));
    }

    private void authenticateNativeOrPair() {
        if (!isOnline()) {
            showOffline("Aucun réseau détecté. Vérifie la 4G/5G, le Wi-Fi et Tailscale.");
            return;
        }
        String nativeToken = JarvisSettings.nativeToken(this);
        if (nativeToken.isEmpty()) {
            showPairingDialog(false);
            return;
        }
        progress.setVisibility(View.VISIBLE);
        new JarvisApi(this).createWebSession(result -> runOnUiThread(() -> {
            if (result.ok && result.cookie != null && !result.cookie.isEmpty()) {
                CookieManager.getInstance().setCookie(serverUrl, result.cookie, value -> {
                    CookieManager.getInstance().flush();
                    initializeFcm();
                    resumePersistentFeatures();
                    loadJarvis();
                });
            } else if (result.status == 401) {
                JarvisSettings.clearNativeToken(this);
                showPairingDialog(false);
            } else {
                showOffline("Authentification native impossible : " + safeError(result.error));
            }
        }));
    }

    private void showPairingDialog(boolean cancelable) {
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setHint("000000");
        input.setInputType(InputType.TYPE_CLASS_NUMBER);
        input.setPadding(dp(18), dp(12), dp(18), dp(12));
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("Appairer ce Galaxy")
                .setMessage("Ouvre l'interface web JARVIS (navigateur, sur le Mac), onglet « Téléphone » du menu principal, puis « Générer un code ». Saisis ici les six chiffres affichés.")
                .setView(input)
                .setCancelable(cancelable)
                .setNegativeButton(cancelable ? "Annuler" : "Adresse du serveur", (d, which) -> {
                    if (!cancelable) showServerDialog(false);
                })
                .setPositiveButton("Appairer", null)
                .create();
        dialog.setOnShowListener(unused -> dialog.getButton(AlertDialog.BUTTON_POSITIVE)
                .setOnClickListener(v -> {
                    String code = input.getText().toString().trim();
                    if (code.length() != 6) {
                        input.setError("Code à six chiffres requis");
                        return;
                    }
                    dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(false);
                    new JarvisApi(this).completePairing(code, result -> runOnUiThread(() -> {
                        if (result.ok) {
                            String token = result.json.optString("token", "");
                            if (!token.isEmpty()) {
                                JarvisSettings.setNativeToken(this, token);
                                dialog.dismiss();
                                authenticateNativeOrPair();
                                return;
                            }
                        }
                        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(true);
                        input.setError(safeError(result.error));
                    }));
                }));
        dialog.show();
    }

    private void showServerDialog(boolean cancelable) {
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setText(serverUrl);
        input.setSelectAllOnFocus(true);
        input.setPadding(dp(18), dp(12), dp(18), dp(12));
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("Connexion à JARVIS")
                .setMessage("Adresse Tailscale HTTPS du Mac. Le certificat privé exact est déjà intégré à l'application.")
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
                        input.setError("Adresse HTTPS valide requise");
                        return;
                    }
                    boolean changed = !normalized.equals(serverUrl);
                    serverUrl = normalized;
                    preferences.edit().putString(JarvisSettings.PREF_SERVER, serverUrl).apply();
                    if (changed) {
                        JarvisSettings.clearNativeToken(this);
                        CookieManager.getInstance().removeAllCookies(null);
                    }
                    dialog.dismiss();
                    authenticateNativeOrPair();
                }));
        dialog.show();
    }

    private void showSettings() {
        boolean location = preferences.getBoolean(JarvisSettings.PREF_LOCATION, false);
        boolean wake = preferences.getBoolean(JarvisSettings.PREF_WAKE, false);
        String[] choices = {
                "Adresse du serveur",
                "Réappairer le téléphone",
                (location ? "Désactiver" : "Activer") + " la présence GPS",
                (wake ? "Désactiver" : "Activer") + " le mot JARVIS"
        };
        new AlertDialog.Builder(this)
                .setTitle("Réglages JARVIS")
                .setItems(choices, (dialog, which) -> {
                    if (which == 0) showServerDialog(true);
                    else if (which == 1) {
                        JarvisSettings.clearNativeToken(this);
                        CookieManager.getInstance().removeAllCookies(null);
                        showPairingDialog(true);
                    } else if (which == 2) toggleLocation();
                    else toggleWakeWord();
                })
                .show();
    }

    private void toggleLocation() {
        if (preferences.getBoolean(JarvisSettings.PREF_LOCATION, false)) {
            preferences.edit().putBoolean(JarvisSettings.PREF_LOCATION, false).apply();
            stopService(new Intent(this, JarvisLocationService.class));
            updateCapabilities();
            return;
        }
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION,
                    Manifest.permission.ACCESS_COARSE_LOCATION}, REQ_LOCATION_FOREGROUND);
            return;
        }
        requestBackgroundLocationOrStart();
    }

    private void requestBackgroundLocationOrStart() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R
                && checkSelfPermission(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
                != PackageManager.PERMISSION_GRANTED) {
            locationSettingsPending = true;
            new AlertDialog.Builder(this)
                    .setTitle("Position en arrière-plan")
                    .setMessage("Dans Autorisations > Position, choisis « Toujours autoriser ». C'est nécessaire pour que JARVIS reste présent application fermée.")
                    .setNegativeButton("Annuler", null)
                    .setPositiveButton("Ouvrir les réglages", (d, w) -> startActivity(
                            new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                                    Uri.parse("package:" + getPackageName()))))
                    .show();
            return;
        }
        if (Build.VERSION.SDK_INT == Build.VERSION_CODES.Q
                && checkSelfPermission(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.ACCESS_BACKGROUND_LOCATION},
                    REQ_LOCATION_BACKGROUND);
            return;
        }
        enableLocationService();
    }

    private void enableLocationService() {
        preferences.edit().putBoolean(JarvisSettings.PREF_LOCATION, true).apply();
        startForegroundService(new Intent(this, JarvisLocationService.class));
        updateCapabilities();
    }

    private void toggleWakeWord() {
        if (preferences.getBoolean(JarvisSettings.PREF_WAKE, false)) {
            preferences.edit().putBoolean(JarvisSettings.PREF_WAKE, false).apply();
            stopService(new Intent(this, JarvisWakeWordService.class));
            updateCapabilities();
            return;
        }
        if (JarvisSettings.porcupineAccessKey(this).isEmpty()) {
            promptPorcupineKey();
            return;
        }
        requestWakeMicrophoneOrStart();
    }

    private void promptPorcupineKey() {
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setHint("Picovoice AccessKey");
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        new AlertDialog.Builder(this)
                .setTitle("Clé locale du mot JARVIS")
                .setMessage("La détection reste sur le téléphone. Colle l'AccessKey gratuite créée sur Picovoice Console.")
                .setView(input)
                .setNegativeButton("Annuler", null)
                .setPositiveButton("Enregistrer", (d, w) -> {
                    String key = input.getText().toString().trim();
                    if (!key.isEmpty()) {
                        JarvisSettings.setPorcupineAccessKey(this, key);
                        requestWakeMicrophoneOrStart();
                    }
                })
                .show();
    }

    private void requestWakeMicrophoneOrStart() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_WAKE_MIC);
            return;
        }
        preferences.edit().putBoolean(JarvisSettings.PREF_WAKE, true).apply();
        startForegroundService(new Intent(this, JarvisWakeWordService.class));
        updateCapabilities();
    }

    private void resumePersistentFeatures() {
        if (preferences.getBoolean(JarvisSettings.PREF_LOCATION, false)
                && checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                == PackageManager.PERMISSION_GRANTED) {
            startForegroundService(new Intent(this, JarvisLocationService.class));
        }
        if (preferences.getBoolean(JarvisSettings.PREF_WAKE, false)
                && checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED) {
            startForegroundService(new Intent(this, JarvisWakeWordService.class));
        }
        updateCapabilities();
    }

    private void updateCapabilities() {
        if (JarvisSettings.nativeToken(this).isEmpty()) return;
        new JarvisApi(this).updateCapabilities(
                preferences.getBoolean(JarvisSettings.PREF_LOCATION, false),
                preferences.getBoolean(JarvisSettings.PREF_WAKE, false)
        );
    }

    private void initializeFcm() {
        if (!BuildConfig.FIREBASE_CONFIGURED) return;
        if (FirebaseApp.initializeApp(this) == null) return;
        FirebaseMessaging.getInstance().getToken().addOnSuccessListener(token -> {
            if (token != null && !token.isEmpty()) new JarvisApi(this).registerPushToken(token);
        });
    }

    private void requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQ_NOTIFICATIONS);
        }
    }

    private void loadJarvis() {
        offlinePanel.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
        progress.setVisibility(View.VISIBLE);
        String path = openVoice ? "/voice" : "/chat";
        openVoice = false;
        webView.loadUrl(serverUrl + path + "?jarvis_android=1");
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

    private String normalizeUrl(String raw) {
        String value = raw == null ? "" : raw.trim();
        if (value.isEmpty()) return null;
        if (!value.startsWith("https://")) value = "https://" + value;
        try {
            URI uri = new URI(value);
            if (uri.getHost() == null || !"https".equalsIgnoreCase(uri.getScheme())) return null;
            return "https://" + uri.getRawAuthority();
        } catch (URISyntaxException ignored) {
            return null;
        }
    }

    private String safeError(String error) {
        return error == null || error.isEmpty() ? "erreur inconnue" : error;
    }

    private final class JarvisWebClient extends WebViewClient {
        @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
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
            handler.cancel();
            showOffline("Certificat HTTPS refusé. L'application n'accepte que le certificat JARVIS intégré.");
        }
    }

    private final class JarvisChromeClient extends WebChromeClient {
        @Override public void onPermissionRequest(PermissionRequest request) {
            runOnUiThread(() -> handleWebPermission(request));
        }

        @Override public void onGeolocationPermissionsShowPrompt(
                String origin, GeolocationPermissions.Callback callback) {
            if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                    == PackageManager.PERMISSION_GRANTED) {
                callback.invoke(origin, true, false);
            } else {
                geoOrigin = origin;
                geoCallback = callback;
                requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION,
                        Manifest.permission.ACCESS_COARSE_LOCATION}, REQ_WEB_GEO);
            }
        }

        @Override public boolean onShowFileChooser(
                WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
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
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                        == PackageManager.PERMISSION_GRANTED) allowed.add(resource);
            }
        }
        if (audioRequested && allowed.isEmpty()) {
            pendingWebPermission = request;
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_WEB_MIC);
        } else if (!allowed.isEmpty()) request.grant(allowed.toArray(new String[0]));
        else request.deny();
    }

    @Override public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
        boolean granted = results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED;
        if (code == REQ_WEB_MIC && pendingWebPermission != null) {
            PermissionRequest request = pendingWebPermission;
            pendingWebPermission = null;
            if (granted) request.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});
            else request.deny();
        } else if (code == REQ_WEB_GEO && geoCallback != null) {
            geoCallback.invoke(geoOrigin, granted, false);
            geoCallback = null;
            geoOrigin = null;
        } else if (code == REQ_LOCATION_FOREGROUND && granted) {
            requestBackgroundLocationOrStart();
        } else if (code == REQ_LOCATION_BACKGROUND && granted) {
            enableLocationService();
        } else if (code == REQ_WAKE_MIC && granted) {
            requestWakeMicrophoneOrStart();
        }
    }

    @Override protected void onResume() {
        super.onResume();
        if (locationSettingsPending) {
            locationSettingsPending = false;
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R
                    || checkSelfPermission(Manifest.permission.ACCESS_BACKGROUND_LOCATION)
                    == PackageManager.PERMISSION_GRANTED) {
                enableLocationService();
            }
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

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
