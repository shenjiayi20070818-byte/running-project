package o00OoOO0.OooOOOO.sensorlab;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.location.GnssStatus;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.concurrent.locks.LockSupport;

/** Local platform-test client; it never changes SensorService operating mode. */
public class InputInstrumentation extends Instrumentation implements SensorEventListener, LocationListener {
    Bundle arguments;
    Context context;
    SensorManager sensors;
    LocationManager locations;
    BufferedWriter output;
    Method initMethod;
    Method injectMethod;
    File stopFlag;
    String runId = "";
    volatile int received = 0, fixes = 0, satelliteCallbacks = 0;
    int attempted = 0, accepted = 0;
    boolean injectionInitialized = false, listenerRegistered = false, locationRegistered = false;
    volatile float[] latestValues = new float[] {0f, 0f, 0f};
    final Handler mainHandler = new Handler(Looper.getMainLooper());
    final GnssStatus.Callback gnss = new GnssStatus.Callback() {
        @Override public void onSatelliteStatusChanged(GnssStatus status) {
            satelliteCallbacks++;
            int used = 0;
            JSONArray list = new JSONArray();
            for (int i = 0; i < status.getSatelliteCount(); i++) {
                if (status.usedInFix(i)) used++;
                JSONObject item = new JSONObject();
                put(item, "svid", status.getSvid(i));
                put(item, "cn0", status.getCn0DbHz(i));
                put(item, "used", status.usedInFix(i));
                list.put(item);
            }
            JSONObject row = event("gnss");
            put(row, "visible", status.getSatelliteCount());
            put(row, "used", used);
            put(row, "satellites", list);
            log(row);
        }
    };
    @Override public void onCreate(Bundle args) {
        super.onCreate(args);
        arguments = args == null ? new Bundle() : args;
        start();
    }
    double number(String key, double fallback, double low, double high) {
        String value = arguments.getString(key);
        double parsed = value == null ? fallback : Double.parseDouble(value);
        if (Double.isNaN(parsed) || Double.isInfinite(parsed) || parsed < low || parsed > high)
            throw new IllegalArgumentException("Invalid " + key + "; expected " + low + ".." + high);
        return parsed;
    }
    double cadenceAt(double seconds, double base, double variation) {
        return Math.max(0.0, Math.min(240.0, base + variation * Math.sin(2 * Math.PI * seconds / 45.0)
            + (variation / 3.0) * Math.sin(2 * Math.PI * seconds / 7.0)));
    }
    void progress(double elapsed, double cadence, double rate, String state) {
        if (context == null) return;
        JSONObject row = event("progress");
        put(row, "state", state); put(row, "elapsed_seconds", elapsed);
        put(row, "injected", attempted); put(row, "accepted", accepted);
        put(row, "cadence_spm", cadence); put(row, "rate_hz", rate);
        put(row, "observed_rate_hz", elapsed > 0 ? attempted / elapsed : 0);
        File temporary = new File(context.getFilesDir(), "sensor-progress.json.tmp");
        File destination = new File(context.getFilesDir(), "sensor-progress.json");
        try {
            BufferedWriter writer = new BufferedWriter(new OutputStreamWriter(new FileOutputStream(temporary, false), "UTF-8"));
            writer.write(row.toString()); writer.close();
            if (!temporary.renameTo(destination)) throw new java.io.IOException("Cannot publish progress file");
        } catch (Exception error) { logError("progress", error); }
    }
    @Override public void onStart() {
        JSONObject summary = event("summary");
        Throwable failure = null;
        boolean closeInjectionSucceeded = false;
        boolean showUi = false;
        boolean liveProgress = true, stopped = false;
        double currentCadence = 0, requestedRate = 50, elapsedSeconds = 0;
        Activity activity = null;
        try {
            context = getTargetContext();
            runId = arguments.getString("run_id", java.util.UUID.randomUUID().toString());
            if (!runId.matches("[A-Za-z0-9_.-]{1,100}")) throw new IllegalArgumentException("Invalid run_id");
            put(summary, "run_id", runId);
            put(summary, "location_observation_enabled", false);
            output = new BufferedWriter(new OutputStreamWriter(new FileOutputStream(
                new File(context.getFilesDir(), "sensor-selftest.jsonl"), false), "UTF-8"));
            stopFlag = new File(context.getFilesDir(), "stop.flag");
            if (stopFlag.exists() && !stopFlag.delete()) throw new java.io.IOException("Cannot clear previous stop.flag");
            double duration = number("duration", 10.0, 1.0, 1800.0);
            double cadence = number("cadence", 180.0, 0.0, 240.0);
            double variation = number("cadence_variation", 6.0, 0.0, 18.0);
            if (cadence == 0) variation = 0;
            double amplitude = number("amplitude", 3.2, 0.0, 8.0);
            double rate = number("rate", 50.0, 2.0, 100.0);
            requestedRate = rate;
            String uiValue = arguments.getString("show_ui", "false");
            if (!uiValue.equals("true") && !uiValue.equals("false"))
                throw new IllegalArgumentException("show_ui must be true or false");
            showUi = uiValue.equals("true");
            String progressValue = arguments.getString("progress", "true");
            if (!progressValue.equals("true") && !progressValue.equals("false"))
                throw new IllegalArgumentException("progress must be true or false");
            liveProgress = progressValue.equals("true");
            put(summary, "show_ui", showUi);
            put(summary, "live_progress", liveProgress);
            put(summary, "package", context.getPackageName());
            put(summary, "duration_seconds", duration);
            put(summary, "cadence_spm", cadence);
            put(summary, "cadence_variation_spm", variation);
            put(summary, "cadence_model", "base + variation*sin(2pi*t/45) + variation/3*sin(2pi*t/7); trapezoidal phase integration");
            put(summary, "amplitude_ms2", amplitude);
            put(summary, "requested_rate_hz", rate);
            put(summary, "axis", "Y");
            put(summary, "gravity_ms2", 9.8);
            log(event("start"));
            if (showUi) activity = startActivitySync(new Intent(context, TestActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            sensors = (SensorManager) context.getSystemService(Context.SENSOR_SERVICE);
            // GPS observation is provided by the separate GNSS helper, not this client.
            final Sensor accelerometer = sensors.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
            if (accelerometer == null) throw new IllegalStateException("No accelerometer");
            initMethod = SensorManager.class.getMethod("initDataInjection", boolean.class);
            injectMethod = SensorManager.class.getMethod("injectSensorData", Sensor.class,
                float[].class, int.class, long.class);
            Method capability = Sensor.class.getMethod("isDataInjectionSupported");
            put(summary, "sensor_injection_supported", capability.invoke(accelerometer));
            put(summary, "sensor_name", accelerometer.getName());
            injectionInitialized = Boolean.TRUE.equals(initMethod.invoke(sensors, true));
            put(summary, "injection_initialized", injectionInitialized);
            if (!injectionInitialized) throw new IllegalStateException("Platform data injection initialization returned false");
            runOnMainSync(new Runnable() { public void run() {
                listenerRegistered = sensors.registerListener(InputInstrumentation.this,
                    accelerometer, 20000, mainHandler);
            }});
            put(summary, "listener_registered", listenerRegistered);
            long start = SystemClock.elapsedRealtimeNanos();
            long period = Math.round(1_000_000_000.0 / rate);
            long end = start + Math.round(duration * 1_000_000_000.0);
            long next = start;
            long previousSample = start, nextProgress = start;
            double phase = 0, previousCadence = cadenceAt(0, cadence, variation);
            double minCadence = Double.POSITIVE_INFINITY, maxCadence = Double.NEGATIVE_INFINITY;
            while (SystemClock.elapsedRealtimeNanos() < end) {
                if (stopFlag.isFile()) { stopped = true; log(event("stop_requested")); break; }
                long now = SystemClock.elapsedRealtimeNanos();
                if (now < next) { LockSupport.parkNanos(next - now); continue; }
                double seconds = (now - start) / 1_000_000_000.0;
                elapsedSeconds = seconds;
                currentCadence = cadenceAt(seconds, cadence, variation);
                minCadence = Math.min(minCadence, currentCadence);
                maxCadence = Math.max(maxCadence, currentCadence);
                phase += 2 * Math.PI * (previousCadence + currentCadence) / 120.0
                    * (now - previousSample) / 1_000_000_000.0;
                previousSample = now; previousCadence = currentCadence;
                float y = (float) (9.8 + amplitude * Math.sin(phase));
                float[] values = new float[] {0f, y, 0f};
                attempted++;
                boolean ok = Boolean.TRUE.equals(injectMethod.invoke(sensors, accelerometer, values,
                    SensorManager.SENSOR_STATUS_ACCURACY_HIGH, now));
                if (ok) accepted++;
                JSONObject row = event("injected");
                put(row, "timestampNs", now); put(row, "values", array(values)); put(row, "accepted", ok);
                put(row, "cadence_spm", currentCadence); put(row, "phase_radians", phase); log(row);
                if (!ok) throw new IllegalStateException("injectSensorData returned false at sample " + attempted);
                next += period;
                if (now - next > period) next = now + period; // no burst catch-up on a slow host
                if (liveProgress && now >= nextProgress) {
                    progress(seconds, currentCadence, rate, "running");
                    nextProgress = now + 1_000_000_000L;
                }
                if (showUi && attempted % Math.max(1, (int) rate) == 0) {
                    TestActivity.show("Local Step Lab\n\nInjected: " + accepted + "/" + attempted
                        + "\nReceived ordinary events: " + received + "\nCadence: " + currentCadence
                        + " spm\nRate: " + rate + " Hz\n\nLocal test only; production APK unchanged.");
                }
            }
            put(summary, "actual_injection_elapsed_ns", SystemClock.elapsedRealtimeNanos() - start);
            elapsedSeconds = (SystemClock.elapsedRealtimeNanos() - start) / 1_000_000_000.0;
            put(summary, "actual_cadence_min_spm", attempted > 0 ? minCadence : 0);
            put(summary, "actual_cadence_max_spm", attempted > 0 ? maxCadence : 0);
            put(summary, "integrated_cycles", phase / (2 * Math.PI));
            SystemClock.sleep(350);
        } catch (Throwable error) {
            failure = error instanceof InvocationTargetException && error.getCause() != null ? error.getCause() : error;
            put(summary, "error", failure.toString());
            logError("test", failure);
        } finally {
            if (injectionInitialized && sensors != null && injectMethod != null) {
                try {
                    Sensor accelerometer = sensors.getDefaultSensor(Sensor.TYPE_ACCELEROMETER);
                    long now = SystemClock.elapsedRealtimeNanos();
                    float[] neutral = new float[] {0f, 9.8f, 0f};
                    boolean neutralAccepted = Boolean.TRUE.equals(injectMethod.invoke(sensors,
                        accelerometer, neutral, SensorManager.SENSOR_STATUS_ACCURACY_HIGH, now));
                    put(summary, "neutral_accepted", neutralAccepted);
                    JSONObject row = event("neutral"); put(row, "timestampNs", now);
                    put(row, "values", array(neutral)); put(row, "accepted", neutralAccepted); log(row);
                    SystemClock.sleep(80);
                } catch (Throwable error) { logError("neutral", error); }
            }
            if (sensors != null) sensors.unregisterListener(this);
            if (locations != null && locationRegistered) {
                locations.removeUpdates(this);
                locations.unregisterGnssStatusCallback(gnss);
            }
            if (sensors != null && initMethod != null) {
                try { closeInjectionSucceeded = Boolean.TRUE.equals(initMethod.invoke(sensors, false)); }
                catch (Throwable error) { logError("close_injection_client", error); }
            }
            put(summary, "attempted", attempted); put(summary, "accepted", accepted);
            put(summary, "received_acceleration_events", received); put(summary, "location_callbacks", fixes);
            put(summary, "gnss_callbacks", satelliteCallbacks); put(summary, "latest_values", array(latestValues));
            put(summary, "client_closed", closeInjectionSucceeded);
            put(summary, "host_must_restore_sensorservice", true);
            put(summary, "stopped", stopped);
            if (liveProgress) progress(elapsedSeconds, currentCadence, requestedRate,
                failure != null ? "error" : (stopped ? "stopped" : "finished"));
            log(summary);
            try {
                if (context != null) {
                    BufferedWriter file = new BufferedWriter(new OutputStreamWriter(new FileOutputStream(
                        new File(context.getFilesDir(), "sensor-selftest-summary.json"), false), "UTF-8"));
                    file.write(summary.toString(2)); file.close();
                }
                if (output != null) output.close();
            } catch (Exception ignored) { }
            if (showUi) TestActivity.show("Local Step Lab\n\n" + summary.toString());
            Bundle result = new Bundle(); result.putString("summary_json", summary.toString());
            finish(failure == null ? Activity.RESULT_OK : Activity.RESULT_CANCELED, result);
        }
    }
    @Override public void onSensorChanged(SensorEvent sensor) {
        if (sensor.sensor.getType() != Sensor.TYPE_ACCELEROMETER) return;
        received++;
        latestValues = sensor.values.clone();
        JSONObject row = event("received_acceleration");
        put(row, "sensorTimestampNs", sensor.timestamp); put(row, "values", array(sensor.values));
        double norm = 0; for (float value : sensor.values) norm += value * value;
        put(row, "norm", Math.sqrt(norm)); log(row);
    }
    @Override public void onAccuracyChanged(Sensor sensor, int accuracy) { }
    @Override public void onLocationChanged(Location location) {
        fixes++;
        JSONObject row = event("location"); put(row, "lat", location.getLatitude()); put(row, "lon", location.getLongitude());
        put(row, "locationTimeMs", location.getTime()); put(row, "locationElapsedNs", location.getElapsedRealtimeNanos());
        put(row, "accuracy", location.getAccuracy()); put(row, "speed", location.getSpeed());
        put(row, "mock", location.isFromMockProvider()); log(row);
    }
    @Override public void onProviderEnabled(String provider) { }
    @Override public void onProviderDisabled(String provider) { }
    @Override public void onStatusChanged(String provider, int status, Bundle extras) { }
    static JSONArray array(float[] values) { JSONArray result = new JSONArray(); for (float value : values) result.put(Float.valueOf(value)); return result; }
    JSONObject event(String kind) { JSONObject row = new JSONObject(); put(row, "kind", kind); put(row, "run_id", runId); put(row, "elapsedMs", SystemClock.elapsedRealtime()); return row; }
    static void put(JSONObject row, String key, Object value) { try { row.put(key, value); } catch (Exception ignored) { } }
    synchronized void log(JSONObject row) { try { if (output != null) { output.write(row.toString()); output.newLine(); output.flush(); } } catch (Exception ignored) { } }
    void logError(String stage, Throwable error) { JSONObject row = event("error"); put(row, "stage", stage); put(row, "error", error.toString()); log(row); }
}
