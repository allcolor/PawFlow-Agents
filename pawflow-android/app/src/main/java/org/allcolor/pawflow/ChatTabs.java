package org.allcolor.pawflow;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

final class ChatTabs<T> {
    private final List<T> items = new ArrayList<>();
    private int activeIndex = -1;

    int add(T item) {
        items.add(item);
        activeIndex = items.size() - 1;
        return activeIndex;
    }

    void activate(int index) {
        if (index < 0 || index >= items.size()) {
            throw new IllegalArgumentException("Invalid tab index");
        }
        activeIndex = index;
    }

    T close(int index) {
        if (index < 0 || index >= items.size()) {
            throw new IllegalArgumentException("Invalid tab index");
        }
        T removed = items.remove(index);
        if (items.isEmpty()) {
            activeIndex = -1;
        } else if (index < activeIndex) {
            activeIndex--;
        } else if (activeIndex >= items.size()) {
            activeIndex = items.size() - 1;
        }
        return removed;
    }

    T active() {
        return activeIndex < 0 ? null : items.get(activeIndex);
    }

    int activeIndex() {
        return activeIndex;
    }

    int size() {
        return items.size();
    }

    List<T> clear() {
        List<T> removed = new ArrayList<>(items);
        items.clear();
        activeIndex = -1;
        return removed;
    }

    List<T> items() {
        return Collections.unmodifiableList(items);
    }
}
