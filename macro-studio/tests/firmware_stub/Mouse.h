#pragma once
#include <cstdint>

constexpr uint8_t MOUSE_LEFT = 1, MOUSE_RIGHT = 2, MOUSE_MIDDLE = 4, MOUSE_ALL = 7;
struct MouseStub {
  uint8_t held = 0;
  int x = 0;
  void begin() {}
  void press(uint8_t mask) { held |= mask; }
  void release(uint8_t mask) { held &= ~mask; }
  void move(int8_t dx, int8_t, int8_t) { x += dx; }
};
extern MouseStub Mouse;
