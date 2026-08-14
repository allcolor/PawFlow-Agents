package org.allcolor.pawflow;

import java.net.URI;
import java.util.Locale;
import java.util.UUID;

final class ServerProfile {
    final String id;
    final String name;
    final String baseUrl;
    final String gatewayKey;

    ServerProfile(String id, String name, String baseUrl, String gatewayKey) {
        this.id = id == null || id.isBlank() ? UUID.randomUUID().toString() : id;
        this.name = require(name, "Server name");
        this.baseUrl = normalizeUrl(baseUrl);
        this.gatewayKey = require(gatewayKey, "Gateway key");
    }

    static String normalizeUrl(String raw) {
        try {
            URI uri = URI.create(require(raw, "Server URL"));
            if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null
                    || uri.getUserInfo() != null || uri.getFragment() != null) {
                throw new IllegalArgumentException("A valid HTTPS server URL is required");
            }
            String path = uri.getPath();
            if (path != null && !path.isBlank() && !"/".equals(path)) {
                throw new IllegalArgumentException("The server URL must not contain a path");
            }
            int port = uri.getPort();
            return "https://" + uri.getHost().toLowerCase(Locale.ROOT)
                    + (port == -1 ? "" : ":" + port);
        } catch (RuntimeException error) {
            if (error instanceof IllegalArgumentException
                    && error.getMessage() != null
                    && error.getMessage().startsWith("A valid")) {
                throw error;
            }
            throw new IllegalArgumentException("A valid HTTPS server URL is required");
        }
    }

    private static String require(String value, String label) {
        String cleaned = value == null ? "" : value.trim();
        if (cleaned.isEmpty()) {
            throw new IllegalArgumentException(label + " is required");
        }
        return cleaned;
    }
}

