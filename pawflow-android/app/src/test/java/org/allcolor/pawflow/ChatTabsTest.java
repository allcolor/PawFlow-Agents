package org.allcolor.pawflow;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

public class ChatTabsTest {
    @Test
    public void keepsIndependentTabsAndActiveSelection() {
        ChatTabs<String> tabs = new ChatTabs<>();
        tabs.add("conversation-a");
        tabs.add("conversation-b");

        assertEquals("conversation-b", tabs.active());
        tabs.activate(0);
        assertEquals("conversation-a", tabs.active());
        assertEquals(2, tabs.size());
    }

    @Test
    public void closingTabsSelectsANeighbourAndClearResetsState() {
        ChatTabs<String> tabs = new ChatTabs<>();
        tabs.add("a");
        tabs.add("b");
        tabs.add("c");
        tabs.activate(1);

        assertEquals("a", tabs.close(0));
        assertEquals("b", tabs.active());
        assertEquals("c", tabs.close(1));
        assertEquals("b", tabs.active());
        assertEquals(1, tabs.clear().size());
        assertNull(tabs.active());
        assertEquals(-1, tabs.activeIndex());
    }
}
