package fr.jarvis.companion;

import android.Manifest;
import android.app.Service;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Build;
import android.os.Bundle;
import android.os.IBinder;

/** Présence GPS à basse fréquence, persistante via service de premier plan. */
public final class JarvisLocationService extends Service implements LocationListener {
    private static final int NOTIFICATION_ID = 4101;
    private static final long MIN_TIME_MS = 5 * 60 * 1000L;
    private static final float MIN_DISTANCE_METERS = 50f;
    private LocationManager locationManager;

    @Override public void onCreate() {
        super.onCreate();
        JarvisNotifications.createChannels(this);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                    NOTIFICATION_ID,
                    JarvisNotifications.foreground(
                            this,
                            JarvisNotifications.PRESENCE,
                            "JARVIS connaît ta position",
                            "Présence GPS active, fréquence économe"
                    ),
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION
            );
        } else {
            startForeground(NOTIFICATION_ID, JarvisNotifications.foreground(
                    this,
                    JarvisNotifications.PRESENCE,
                    "JARVIS connaît ta position",
                    "Présence GPS active, fréquence économe"
            ));
        }
        locationManager = (LocationManager) getSystemService(LOCATION_SERVICE);
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (!hasLocationPermission() || JarvisSettings.nativeToken(this).isEmpty()) {
            stopSelf();
            return START_NOT_STICKY;
        }
        requestUpdates();
        return START_STICKY;
    }

    private boolean hasLocationPermission() {
        return checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)
                == PackageManager.PERMISSION_GRANTED;
    }

    private void requestUpdates() {
        if (locationManager == null || !hasLocationPermission()) return;
        try {
            if (locationManager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                locationManager.requestLocationUpdates(
                        LocationManager.NETWORK_PROVIDER,
                        MIN_TIME_MS,
                        MIN_DISTANCE_METERS,
                        this
                );
            }
            if (locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                locationManager.requestLocationUpdates(
                        LocationManager.GPS_PROVIDER,
                        MIN_TIME_MS,
                        MIN_DISTANCE_METERS,
                        this
                );
            }
        } catch (SecurityException ignored) {
            stopSelf();
        }
    }

    @Override public void onLocationChanged(Location location) {
        new JarvisApi(this).postLocation(
                location.getLatitude(),
                location.getLongitude(),
                location.getAltitude(),
                location.getAccuracy(),
                location.getSpeed(),
                location.getTime()
        );
    }

    @Override public void onProviderEnabled(String provider) {}
    @Override public void onProviderDisabled(String provider) {}
    @Override public void onStatusChanged(String provider, int status, Bundle extras) {}
    @Override public IBinder onBind(Intent intent) { return null; }

    @Override public void onDestroy() {
        if (locationManager != null) locationManager.removeUpdates(this);
        super.onDestroy();
    }
}
