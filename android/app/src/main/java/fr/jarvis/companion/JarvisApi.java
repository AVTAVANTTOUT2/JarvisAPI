package fr.jarvis.companion;

import android.content.Context;
import android.os.Build;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Client HTTPS natif. Le certificat JARVIS est fourni par network_security_config. */
final class JarvisApi {
    interface Callback { void complete(Result result); }

    static final class Result {
        final boolean ok;
        final int status;
        final JSONObject json;
        final String cookie;
        final String error;

        Result(boolean ok, int status, JSONObject json, String cookie, String error) {
            this.ok = ok;
            this.status = status;
            this.json = json;
            this.cookie = cookie;
            this.error = error;
        }
    }

    private static final ExecutorService NETWORK = Executors.newFixedThreadPool(2);
    private final Context context;

    JarvisApi(Context context) {
        this.context = context.getApplicationContext();
    }

    void completePairing(String code, Callback callback) {
        JSONObject body = new JSONObject();
        try {
            body.put("code", code);
            body.put("device_id", JarvisSettings.deviceId(context));
            body.put("name", "JARVIS sur " + Build.MODEL);
            body.put("model", Build.MANUFACTURER + " " + Build.MODEL);
            body.put("app_version", BuildConfig.VERSION_NAME);
        } catch (Exception ignored) {}
        post("/api/mobile/pairing/complete", body, "", callback);
    }

    void createWebSession(Callback callback) {
        post("/api/mobile/session", new JSONObject(), JarvisSettings.nativeToken(context), callback);
    }

    void registerPushToken(String fcmToken) {
        JSONObject body = new JSONObject();
        try { body.put("token", fcmToken); } catch (Exception ignored) {}
        post("/api/mobile/push-token", body, JarvisSettings.nativeToken(context), result -> {});
    }

    void updateCapabilities(boolean location, boolean wakeWord) {
        JSONObject body = new JSONObject();
        try {
            body.put("push", BuildConfig.FIREBASE_CONFIGURED);
            body.put("background_location", location);
            body.put("wake_word", wakeWord);
        } catch (Exception ignored) {}
        post("/api/mobile/capabilities", body, JarvisSettings.nativeToken(context), result -> {});
    }

    void postLocation(double latitude, double longitude, double altitude,
                      float accuracy, float speed, long timestamp) {
        JSONObject body = new JSONObject();
        try {
            body.put("latitude", latitude);
            body.put("longitude", longitude);
            body.put("altitude", altitude);
            body.put("accuracy", accuracy);
            body.put("speed", speed);
            body.put("timestamp", timestamp);
            body.put("source", "android_background");
        } catch (Exception ignored) {}
        post("/api/location", body, JarvisSettings.nativeToken(context), result -> {});
    }

    private void post(String path, JSONObject body, String bearer, Callback callback) {
        NETWORK.execute(() -> callback.complete(request(path, body, bearer)));
    }

    private Result request(String path, JSONObject body, String bearer) {
        HttpURLConnection connection = null;
        try {
            URL url = new URL(JarvisSettings.server(context) + path);
            connection = (HttpURLConnection) url.openConnection();
            connection.setRequestMethod("POST");
            connection.setConnectTimeout(12_000);
            connection.setReadTimeout(20_000);
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("User-Agent", "JARVIS-Android/" + BuildConfig.VERSION_NAME);
            if (bearer != null && !bearer.isEmpty()) {
                connection.setRequestProperty("Authorization", "Bearer " + bearer);
            }
            connection.setDoOutput(true);
            byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
            connection.getOutputStream().write(bytes);

            int status = connection.getResponseCode();
            InputStream stream = status >= 200 && status < 400
                    ? connection.getInputStream() : connection.getErrorStream();
            String text = read(stream);
            JSONObject json = text.isEmpty() ? new JSONObject() : new JSONObject(text);
            String cookie = connection.getHeaderField("Set-Cookie");
            String error = json.optString("detail", json.optString("error", "HTTP " + status));
            return new Result(status >= 200 && status < 300, status, json, cookie, error);
        } catch (Exception e) {
            return new Result(false, 0, new JSONObject(), "", e.getMessage());
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private String read(InputStream stream) throws Exception {
        if (stream == null) return "";
        StringBuilder result = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(
                new InputStreamReader(stream, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) result.append(line);
        }
        return result.toString();
    }
}
