package org.allcolor.pawflow;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

final class PawFlowApi {
    private PawFlowApi() {}

    static JSONObject get(ServerProfile server, String path) throws Exception {
        return request(server, "GET", path, null);
    }

    static JSONObject post(ServerProfile server, String path, JSONObject body) throws Exception {
        return request(server, "POST", path, body);
    }

    private static JSONObject request(ServerProfile server, String method,
                                      String path, JSONObject body) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(
                server.baseUrl + path).openConnection();
        connection.setRequestMethod(method);
        connection.setConnectTimeout(12_000);
        connection.setReadTimeout(20_000);
        connection.setInstanceFollowRedirects(false);
        connection.setRequestProperty("Accept", "application/json");
        connection.setRequestProperty("X-PawFlow-Gateway-Key", server.gatewayKey);
        if (body != null) {
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            byte[] payload = body.toString().getBytes(StandardCharsets.UTF_8);
            try (OutputStream output = connection.getOutputStream()) {
                output.write(payload);
            }
        }
        int status = connection.getResponseCode();
        InputStream stream = status >= 200 && status < 300
                ? connection.getInputStream() : connection.getErrorStream();
        StringBuilder text = new StringBuilder();
        if (stream != null) {
            try (BufferedReader reader = new BufferedReader(
                    new InputStreamReader(stream, StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    text.append(line);
                }
            }
        }
        connection.disconnect();
        JSONObject result = text.length() == 0 ? new JSONObject() : new JSONObject(text.toString());
        if (status < 200 || status >= 300) {
            throw new IllegalStateException(result.optString("error", "HTTP " + status));
        }
        return result;
    }
}

