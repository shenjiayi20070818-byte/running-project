package com.zjwh.android_wh_physicalfitness.sensorlab;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.location.GnssStatus;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.util.UUID;

/** An observable foreground GPS subscription; no UI, internet, or input injection. */
public class GnssKeepAliveService extends Service implements LocationListener {
    static final String CHANNEL = "sensorlab_gnss_observation";
    static final int NOTIFICATION = 1733;
    final Handler handler = new Handler(Looper.getMainLooper());
    final String session = UUID.randomUUID().toString();
    LocationManager locationManager;
    BufferedWriter log;
    boolean gpsRequested = false, callbackRegistered = false, running = false;
    int locationCount = 0, gnssCount = 0, visible = 0, used = 0;
    long lastLocationElapsedNs = 0, lastCallbackElapsedMs = 0, lastLocationTimeMs = 0;
    double lat = 0, lon = 0;
    String provider = "", error = "";
    final GnssStatus.Callback gnss = new GnssStatus.Callback() {
        @Override public void onSatelliteStatusChanged(GnssStatus status) {
            visible = status.getSatelliteCount(); used = 0; gnssCount++;
            JSONArray list = new JSONArray();
            for (int i = 0; i < visible; i++) {
                if (status.usedInFix(i)) used++;
                JSONObject satellite = new JSONObject();
                put(satellite, "svid", status.getSvid(i));
                put(satellite, "cn0", status.getCn0DbHz(i));
                put(satellite, "used", status.usedInFix(i)); list.put(satellite);
            }
            JSONObject row = event("gnss"); put(row, "visible", visible); put(row, "used", used);
            put(row, "satellites", list); append(row); publish();
        }
        @Override public void onStarted() { append(event("gnss_started")); }
        @Override public void onStopped() { append(event("gnss_stopped")); }
    };
    final Runnable heartbeat = new Runnable() {
        @Override public void run() { publish(); if (running) handler.postDelayed(this, 1000); }
    };
    @Override public void onCreate() {
        super.onCreate();
        locationManager = (LocationManager) getSystemService(LOCATION_SERVICE);
        try { log = new BufferedWriter(new OutputStreamWriter(new FileOutputStream(
            new File(getFilesDir(), "gnss-observer.jsonl"), true), "UTF-8")); }
        catch (Exception problem) { error = problem.toString(); }
    }
    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ("stop".equals(intent.getAction()) || "STOP".equals(intent.getAction()))) {
            stopSelf(); return START_NOT_STICKY;
        }
        if (running) { publish(); return START_NOT_STICKY; }
        try {
            NotificationManager notifications = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            notifications.createNotificationChannel(new NotificationChannel(CHANNEL,
                "Local GNSS observation", NotificationManager.IMPORTANCE_LOW));
            Notification notification = new Notification.Builder(this, CHANNEL)
                .setSmallIcon(android.R.drawable.ic_menu_mylocation)
                .setContentTitle("Local GNSS test observer")
                .setContentText("GPS observation active; no data is uploaded")
                .setOngoing(true).build();
            if (Build.VERSION.SDK_INT >= 29) startForeground(NOTIFICATION, notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION);
            else startForeground(NOTIFICATION, notification);
            if (checkSelfPermission(android.Manifest.permission.ACCESS_FINE_LOCATION) != 0)
                throw new SecurityException("ACCESS_FINE_LOCATION must be granted before starting this test service");
            locationManager.requestLocationUpdates(LocationManager.GPS_PROVIDER, 1000L, 0f,
                this, Looper.getMainLooper());
            gpsRequested = true;
            callbackRegistered = locationManager.registerGnssStatusCallback(gnss, handler);
            running = true; error = "";
            append(event("service_start"));
            handler.post(heartbeat);
        } catch (Exception problem) {
            error = problem.toString(); JSONObject row = event("error"); put(row, "error", error);
            append(row); publish(); stopSelf();
        }
        return START_NOT_STICKY;
    }
    @Override public void onLocationChanged(Location location) {
        locationCount++; lat = location.getLatitude(); lon = location.getLongitude();
        provider = location.getProvider();
        lastLocationElapsedNs = location.getElapsedRealtimeNanos();
        lastLocationTimeMs = location.getTime();
        lastCallbackElapsedMs = SystemClock.elapsedRealtime();
        JSONObject row = event("location"); put(row, "lat", lat); put(row, "lon", lon);
        put(row, "provider", provider); put(row, "locationTimeMs", location.getTime());
        put(row, "locationElapsedNs", lastLocationElapsedNs); put(row, "accuracy", location.getAccuracy());
        put(row, "hasSpeed", location.hasSpeed()); put(row, "speed", location.getSpeed());
        put(row, "bearing", location.getBearing()); put(row, "mock", location.isFromMockProvider());
        append(row); publish();
    }
    @Override public void onProviderEnabled(String provider) { append(event("provider_enabled")); publish(); }
    @Override public void onProviderDisabled(String provider) { append(event("provider_disabled")); publish(); }
    @Override public void onStatusChanged(String provider, int status, Bundle extras) { }
    @Override public IBinder onBind(Intent intent) { return null; }
    @Override public void onDestroy() {
        running = false; handler.removeCallbacks(heartbeat);
        if (locationManager != null) {
            if (gpsRequested) locationManager.removeUpdates(this);
            if (callbackRegistered) locationManager.unregisterGnssStatusCallback(gnss);
        }
        gpsRequested = false; callbackRegistered = false;
        append(event("service_stop")); publish();
        try { if (log != null) log.close(); } catch (Exception ignored) { }
        stopForeground(true);
        super.onDestroy();
    }
    JSONObject event(String kind) {
        JSONObject row = new JSONObject(); put(row, "kind", kind); put(row, "session", session);
        put(row, "elapsedMs", SystemClock.elapsedRealtime());
        put(row, "elapsed_ms", SystemClock.elapsedRealtime()); put(row, "wallTimeMs", System.currentTimeMillis());
        return row;
    }
    void publish() {
        JSONObject row = event("status");
        put(row, "running", running); put(row, "gps_requested", gpsRequested);
        put(row, "gnss_callback_registered", callbackRegistered);
        put(row, "location_count", locationCount); put(row, "gnss_count", gnssCount);
        put(row, "visible", visible); put(row, "used", used);
        put(row, "last_location_elapsed_ns", lastLocationElapsedNs);
        put(row, "last_location_callback_elapsed_ms", lastCallbackElapsedMs);
        put(row, "locationTimeMs", lastLocationTimeMs); put(row, "locationElapsedNs", lastLocationElapsedNs);
        put(row, "lat", lat); put(row, "lon", lon); put(row, "provider", provider); put(row, "error", error);
        put(row, "fine_permission", checkSelfPermission(android.Manifest.permission.ACCESS_FINE_LOCATION) == 0);
        try { put(row, "gps_provider_enabled", locationManager != null
            && locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER)); }
        catch (Exception problem) { put(row, "gps_provider_enabled", false); }
        for (String name : new String[] {"gnss-keepalive.json", "gnss-observer-progress.json"}) {
            File tmp = new File(getFilesDir(), name + ".tmp");
            try {
                BufferedWriter writer = new BufferedWriter(new OutputStreamWriter(new FileOutputStream(tmp, false), "UTF-8"));
                writer.write(row.toString()); writer.close();
                if (!tmp.renameTo(new File(getFilesDir(), name))) error = "Cannot publish " + name;
            } catch (Exception problem) { error = problem.toString(); }
        }
    }
    synchronized void append(JSONObject row) {
        try { if (log != null) { log.write(row.toString()); log.newLine(); log.flush(); } }
        catch (Exception problem) { error = problem.toString(); }
    }
    static void put(JSONObject row, String key, Object value) { try { row.put(key, value); } catch (Exception ignored) { } }
}
