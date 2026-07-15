package fr.jarvis.companion;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Petits secrets chiffrés avec une clé non exportable de l'Android Keystore. */
final class JarvisSecureStore {
    private static final String ALIAS = "jarvis_companion_v1";
    private static final String PREFS = "jarvis_secure";
    private final SharedPreferences preferences;

    JarvisSecureStore(Context context) {
        preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    synchronized void put(String name, String value) {
        try {
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key());
            byte[] encrypted = cipher.doFinal(value.getBytes(StandardCharsets.UTF_8));
            String payload = Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP)
                    + "." + Base64.encodeToString(encrypted, Base64.NO_WRAP);
            preferences.edit().putString(name, payload).apply();
        } catch (Exception e) {
            throw new IllegalStateException("Stockage sécurisé Android indisponible", e);
        }
    }

    synchronized String get(String name) {
        String payload = preferences.getString(name, "");
        if (payload == null || payload.isEmpty()) return "";
        try {
            String[] parts = payload.split("\\.", 2);
            if (parts.length != 2) return "";
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(
                    Cipher.DECRYPT_MODE,
                    key(),
                    new GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP))
            );
            return new String(
                    cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)),
                    StandardCharsets.UTF_8
            );
        } catch (Exception ignored) {
            preferences.edit().remove(name).apply();
            return "";
        }
    }

    synchronized void remove(String name) {
        preferences.edit().remove(name).apply();
    }

    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (store.containsAlias(ALIAS)) {
            return ((KeyStore.SecretKeyEntry) store.getEntry(ALIAS, null)).getSecretKey();
        }
        KeyGenerator generator = KeyGenerator.getInstance(
                KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore"
        );
        generator.init(new KeyGenParameterSpec.Builder(
                ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT
        ).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build());
        return generator.generateKey();
    }
}
