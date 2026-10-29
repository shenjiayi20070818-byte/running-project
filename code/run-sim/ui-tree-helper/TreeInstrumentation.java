package com.codex.localuitree;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.Activity;
import android.app.Instrumentation;
import android.app.UiAutomation;
import android.content.Context;
import android.graphics.Rect;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityNodeInfo;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;

/** One read-only snapshot through the public Android instrumentation API.
 * No Activity, input events, shell execution, network, permissions or hidden APIs.
 * Output can contain the foreground app's personal data; it stays in our own files.
 */
public final class TreeInstrumentation extends Instrumentation {
    private Bundle arguments;
    private final JSONArray nodes = new JSONArray();
    private final JSONArray errors = new JSONArray();
    private int maxNodes = 1500;
    private int maxDepth = 40;
    private boolean truncated;

    @Override public void onCreate(Bundle args) {
        super.onCreate(args);
        arguments = args == null ? new Bundle() : args;
        start();
    }

    private int option(String name, int fallback, int low, int high) {
        String value = arguments.getString(name);
        int parsed = value == null ? fallback : Integer.parseInt(value);
        if (parsed < low || parsed > high) throw new IllegalArgumentException("Invalid " + name);
        return parsed;
    }

    private static String string(CharSequence value) {
        return value == null ? null : value.toString();
    }

    private static void put(JSONObject object, String key, Object value) {
        try { object.put(key, value == null ? JSONObject.NULL : value); }
        catch (Exception impossible) { throw new IllegalStateException(impossible); }
    }

    private void error(String stage, Throwable error) {
        JSONObject row = new JSONObject();
        put(row, "stage", stage);
        put(row, "type", error.getClass().getName());
        put(row, "message", error.getMessage());
        errors.put(row);
    }

    private void visit(AccessibilityNodeInfo node, int parentIndex, int childIndex, int depth) {
        try {
            if (nodes.length() >= maxNodes || depth > maxDepth) {
                truncated = true;
                return;
            }
            final int index = nodes.length();
            JSONObject item = new JSONObject();
            put(item, "index", index);
            put(item, "parentIndex", parentIndex);
            put(item, "childIndex", childIndex);
            put(item, "depth", depth);
            // Add before property reads, so a stale node still leaves a trace.
            nodes.put(item);
            try {
                boolean password = node.isPassword();
                put(item, "passwordTextRedacted", password);
                put(item, "text", password ? null : string(node.getText()));
                put(item, "contentDescription", password ? null : string(node.getContentDescription()));
                put(item, "resourceId", node.getViewIdResourceName());
                put(item, "className", string(node.getClassName()));
                put(item, "packageName", string(node.getPackageName()));
                Rect bounds = new Rect();
                node.getBoundsInScreen(bounds);
                JSONObject rectangle = new JSONObject();
                put(rectangle, "left", bounds.left); put(rectangle, "top", bounds.top);
                put(rectangle, "right", bounds.right); put(rectangle, "bottom", bounds.bottom);
                put(item, "bounds", rectangle);
                put(item, "visibleToUser", node.isVisibleToUser());
                put(item, "enabled", node.isEnabled());
                put(item, "clickable", node.isClickable());
                put(item, "scrollable", node.isScrollable());
                int count = node.getChildCount();
                put(item, "childCount", count);
                if (count > 0 && depth == maxDepth) truncated = true;
                for (int i = 0; i < count && depth < maxDepth; i++) {
                    if (nodes.length() >= maxNodes) { truncated = true; break; }
                    AccessibilityNodeInfo child = null;
                    try {
                        child = node.getChild(i);
                        if (child != null) visit(child, index, i, depth + 1);
                    } catch (RuntimeException failure) { error("child:" + index + ":" + i, failure); }
                    finally { if (child != null) child.recycle(); }
                }
            } catch (RuntimeException failure) { error("node:" + index, failure); }
        } catch (RuntimeException failure) { error("visit", failure); }
    }

    private static void writeOwnFile(Context context, String name, JSONObject report) throws Exception {
        File temporary = new File(context.getFilesDir(), name + ".tmp");
        File destination = new File(context.getFilesDir(), name);
        try (FileOutputStream stream = new FileOutputStream(temporary, false);
             OutputStreamWriter writer = new OutputStreamWriter(stream, StandardCharsets.UTF_8)) {
            writer.write(report.toString(2));
            writer.flush();
            stream.getFD().sync();
        }
        if (!temporary.renameTo(destination)) throw new java.io.IOException("Cannot publish snapshot file");
    }

    @Override public void onStart() {
        long start = SystemClock.elapsedRealtime();
        JSONObject report = new JSONObject();
        Context context = getTargetContext();
        UiAutomation automation = null;
        AccessibilityNodeInfo root = null;
        String name = "ui-tree-latest.json";
        boolean success = false;
        try {
            name = arguments.getString("out", name);
            if (!name.matches("[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\\.json"))
                throw new IllegalArgumentException("out must be a simple .json filename");
            maxNodes = option("max_nodes", 1500, 1, 5000);
            maxDepth = option("max_depth", 40, 1, 80);
            put(report, "schema", "local-ui-tree-v1");
            put(report, "capturedAtEpochMs", System.currentTimeMillis());
            put(report, "capturedAtElapsedMs", start);
            put(report, "observerPackage", context.getPackageName());
            put(report, "waitForIdleUsed", false);
            put(report, "viewActionsPerformed", false);
            put(report, "maxNodes", maxNodes); put(report, "maxDepth", maxDepth);
            automation = getUiAutomation(UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES);
            if (automation == null) throw new IllegalStateException("UiAutomation unavailable");
            AccessibilityServiceInfo info = automation.getServiceInfo();
            if (info != null) {
                // Flags affect this transient observer only, not the foreground application.
                info.flags |= AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS
                    | AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS;
                automation.setServiceInfo(info);
            }
            root = automation.getRootInActiveWindow();
            put(report, "rootAvailable", root != null);
            if (root == null) throw new IllegalStateException("Current active window returned no accessibility root");
            put(report, "rootPackage", string(root.getPackageName()));
            put(report, "windowId", root.getWindowId());
            visit(root, -1, -1, 0);
            success = nodes.length() > 0;
        } catch (Throwable failure) { error("snapshot", failure); }
        finally {
            if (root != null) root.recycle();
            // Instrumentation.finish() owns/disconnects its UiAutomation connection.
            // UiAutomation.destroy() is not in the public API-33 SDK; never reflect it.
            put(report, "success", success);
            put(report, "truncated", truncated);
            put(report, "nodeCount", nodes.length());
            put(report, "nodes", nodes);
            put(report, "errors", errors);
            put(report, "captureDurationMs", SystemClock.elapsedRealtime() - start);
            Bundle result = new Bundle();
            try {
                // Invalid names do not become paths even when reporting an argument error.
                if (!name.matches("[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\\.json")) name = "ui-tree-error.json";
                writeOwnFile(context, name, report);
                result.putString("output_file", new File(context.getFilesDir(), name).getAbsolutePath());
            } catch (Exception failure) {
                success = false;
                result.putString("write_error", failure.toString());
            }
            result.putInt("node_count", nodes.length());
            result.putBoolean("success", success);
            finish(success ? Activity.RESULT_OK : Activity.RESULT_CANCELED, result);
        }
    }
}
