#pragma once
#include <cstddef>
#include <cstdint>
#include <deque>
#include <iostream>
#include <iomanip>

extern uint32_t clockMs;
inline uint32_t millis() { return clockMs; }
inline uint32_t micros() { return clockMs * 1000; }

struct SerialStub {
  std::deque<uint8_t> pending;
  bool connected = true;
  void begin(unsigned long) {}
  bool dtr() const { return connected; }
  int available() const { return pending.size(); }
  int availableForWrite() const { return 64; }
  int read() {
    const uint8_t data = pending.front();
    pending.pop_front();
    return data;
  }
  size_t write(const uint8_t *data, size_t length) {
    for (size_t i = 0; i < length; ++i)
      std::cout << std::hex << std::setw(2) << std::setfill('0') << int(data[i]);
    return length;
  }
};
extern SerialStub Serial;
