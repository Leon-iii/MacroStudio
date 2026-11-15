#pragma once
#include <cstdint>
#include <set>

struct KeyboardStub {
  std::set<uint8_t> held;
  void begin() {}
  int press(uint8_t key) { held.insert(key); return 1; }
  void release(uint8_t key) { held.erase(key); }
  void releaseAll() { held.clear(); }
};
extern KeyboardStub Keyboard;
