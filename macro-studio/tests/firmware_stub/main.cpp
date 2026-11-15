#include "Arduino.h"
#include "Keyboard.h"
#include "Mouse.h"
#include <string>

uint32_t clockMs = 0;
SerialStub Serial;
KeyboardStub Keyboard;
MouseStub Mouse;
void setup();
void loop();

int main() {
  setup();
  uint32_t elapsed;
  bool connected;
  std::string packet;
  while (std::cin >> elapsed >> connected >> packet) {
    clockMs += elapsed;
    Serial.connected = connected;
    for (size_t i = 0; i + 1 < packet.size(); i += 2)
      Serial.pending.push_back(std::stoi(packet.substr(i, 2), nullptr, 16));
    do {
      loop();
    } while (connected && Serial.available());
    std::cout << ' ' << std::dec << Keyboard.held.size() << ' '
              << int(Mouse.held) << ' ' << Mouse.x << '\n';
  }
}
