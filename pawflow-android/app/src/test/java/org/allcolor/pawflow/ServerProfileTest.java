package org.allcolor.pawflow;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

public class ServerProfileTest {
    @Test
    public void normalizesHttpsOrigin() {
        assertEquals("https://paw.example:9443",
                ServerProfile.normalizeUrl("https://PAW.example:9443/"));
    }

    @Test
    public void rejectsCleartextAndPaths() {
        assertThrows(IllegalArgumentException.class,
                () -> ServerProfile.normalizeUrl("http://paw.example"));
        assertThrows(IllegalArgumentException.class,
                () -> ServerProfile.normalizeUrl("https://paw.example/chat"));
    }
}
