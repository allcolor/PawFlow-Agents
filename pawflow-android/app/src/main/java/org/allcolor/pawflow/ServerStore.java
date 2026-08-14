package org.allcolor.pawflow;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

final class ServerStore {
    private static final String PREFS = "pawflow.mobile.servers";
    private static final String SERVERS = "servers";
    private final SharedPreferences preferences;
    private final CryptoStore crypto = new CryptoStore();

    ServerStore(Context context) {
        preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    List<ServerProfile> list() {
        List<ServerProfile> result = new ArrayList<>();
        try {
            JSONArray array = new JSONArray(preferences.getString(SERVERS, "[]"));
            for (int index = 0; index < array.length(); index++) {
                JSONObject item = array.getJSONObject(index);
                result.add(new ServerProfile(
                        item.getString("id"), item.getString("name"),
                        item.getString("url"), crypto.decrypt(item.getString("gateway"))));
            }
        } catch (Exception error) {
            throw new IllegalStateException("Unable to read server profiles", error);
        }
        return result;
    }

    ServerProfile find(String id) {
        for (ServerProfile profile : list()) {
            if (profile.id.equals(id)) {
                return profile;
            }
        }
        return null;
    }

    void save(ServerProfile profile) {
        List<ServerProfile> profiles = list();
        profiles.removeIf(item -> item.id.equals(profile.id));
        profiles.add(profile);
        write(profiles);
    }

    void delete(String id) {
        List<ServerProfile> profiles = list();
        profiles.removeIf(item -> item.id.equals(id));
        write(profiles);
    }

    private void write(List<ServerProfile> profiles) {
        try {
            JSONArray array = new JSONArray();
            for (ServerProfile profile : profiles) {
                JSONObject item = new JSONObject();
                item.put("id", profile.id);
                item.put("name", profile.name);
                item.put("url", profile.baseUrl);
                item.put("gateway", crypto.encrypt(profile.gatewayKey));
                array.put(item);
            }
            preferences.edit().putString(SERVERS, array.toString()).apply();
        } catch (Exception error) {
            throw new IllegalStateException("Unable to save server profiles", error);
        }
    }
}

