#include <Arduino.h>
#include <Keyboard.h>
#include <Mouse.h>
#include <string.h>

enum Opcode : uint8_t {
  HELLO = 1, GET_CAPS, ARM, DISARM, HEARTBEAT, INPUT_EVENT, RELEASE_ALL,
  PREPARE_JOB, START_JOB, CANCEL_JOB, GET_DEVICE_STATUS, QUEUE_BATCH,
  ACK = 0x80, NACK, HELLO_ACK, CAPS, STATUS, JOB_DONE, FAULT
};
enum State : uint8_t { DISARMED, IDLE, ARMED };
enum Error : uint8_t {
  BAD_PAYLOAD = 1, UNSUPPORTED, INVALID_STATE, INVALID_SESSION,
  SEQUENCE_CONFLICT, INVALID_FRAME, WATCHDOG_EXPIRED, INCOMPATIBLE_VERSION
};

constexpr uint8_t HEADER_SIZE = 12;
constexpr uint8_t MAX_PAYLOAD = 64;
constexpr uint8_t MAX_RAW = HEADER_SIZE + MAX_PAYLOAD + 2;
constexpr uint8_t MAX_ENCODED = MAX_RAW + 1;
constexpr uint16_t WATCHDOG_MS = 1500;
uint8_t encoded[MAX_ENCODED];
uint8_t encodedLength;
bool discarding;
State state = DISARMED;
uint32_t session;
uint32_t closedSession;
uint32_t recentSessions[32];
uint8_t nextSessionSlot;
uint32_t lastHeartbeat;
uint8_t lastError;
uint16_t lastSeq;
uint8_t lastRequest[MAX_RAW];
uint8_t lastRequestLength;
Opcode lastReply;
uint8_t lastPayload[24];
uint8_t lastPayloadLength;
struct TapJob {
  uint16_t id;
  uint8_t key;
  uint32_t period, hold, count, sent, next, releaseAt;
  bool running, held, done, batch;
  uint8_t result;
} job;
constexpr uint8_t BATCH_LIMIT = 8;
constexpr uint8_t BATCH_EDGE_SIZE = 7;
uint8_t batchEdges[BATCH_LIMIT * BATCH_EDGE_SIZE];

uint16_t get16(const uint8_t *p) {
  return uint16_t(p[0]) | uint16_t(p[1]) << 8;
}

uint32_t get32(const uint8_t *p) {
  return uint32_t(get16(p)) | uint32_t(get16(p + 2)) << 16;
}

void put16(uint8_t *p, uint16_t value) {
  p[0] = value;
  p[1] = value >> 8;
}

uint16_t crc16(const uint8_t *p, uint8_t length) {
  uint16_t crc = 0xFFFF;
  while (length--) {
    crc ^= uint16_t(*p++) << 8;
    for (uint8_t bit = 0; bit < 8; ++bit)
      crc = crc & 0x8000 ? (crc << 1) ^ 0x1021 : crc << 1;
  }
  return crc;
}

uint8_t decodePacket(uint8_t *raw) {
  uint8_t read = 0;
  uint8_t length = 0;
  while (read < encodedLength) {
    const uint8_t count = encoded[read++] - 1;
    if (count > encodedLength - read || count > MAX_RAW - length) return 0;
    memcpy(raw + length, encoded + read, count);
    read += count;
    length += count;
    if (read < encodedLength) {
      if (length == MAX_RAW) return 0;
      raw[length++] = 0;
    }
  }
  return length;
}

bool sendReply(Opcode op, uint16_t seq, uint32_t token, const uint8_t *payload, uint8_t length) {
  uint8_t raw[HEADER_SIZE + sizeof(lastPayload) + 2] = {1, 0, op, 0};
  put16(raw + 4, seq);
  put16(raw + 6, token);
  put16(raw + 8, token >> 16);
  put16(raw + 10, length);
  memcpy(raw + HEADER_SIZE, payload, length);
  const uint8_t crcOffset = HEADER_SIZE + length;
  put16(raw + crcOffset, crc16(raw, crcOffset));

  uint8_t wire[sizeof(raw) + 2];
  uint8_t codeAt = 0;
  uint8_t out = 1;
  for (uint8_t i = 0; i < crcOffset + 2; ++i) {
    if (raw[i] == 0) {
      wire[codeAt] = out - codeAt;
      codeAt = out++;
    } else {
      wire[out++] = raw[i];
    }
  }
  wire[codeAt] = out - codeAt;
  wire[out++] = 0;
  return Serial.availableForWrite() >= out && Serial.write(wire, out) == out;
}

void finishJob(uint8_t result) {
  if (job.held) Keyboard.release(job.key);
  if (job.batch && result) {
    Keyboard.releaseAll();
    Mouse.release(MOUSE_ALL);
  }
  job.held = job.running = false;
  job.done = true;
  job.result = result;
}

Error applyInput(const uint8_t *p, uint8_t size);

void tickBatch(uint32_t now) {
  const uint8_t *edge = batchEdges + job.sent * BATCH_EDGE_SIZE;
  if (int32_t(now - (job.next + get32(edge))) < 0) return;
  const uint8_t kind = edge[4];
  Error error;
  if (kind <= 2) {
    const uint8_t key[] = {1, kind, 7, 0, edge[5], 0};
    error = applyInput(key, sizeof(key));
  } else if (kind <= 4) {
    const uint8_t button[] = {2, uint8_t(kind - 2), edge[5]};
    error = applyInput(button, sizeof(button));
  } else {
    const int8_t x = edge[5], y = edge[6];
    const uint8_t move[] = {3, edge[5], uint8_t(x < 0 ? 255 : 0), edge[6], uint8_t(y < 0 ? 255 : 0)};
    error = applyInput(move, sizeof(move));
  }
  if (error) finishJob(2);
  else if (++job.sent == job.count) finishJob(0);
}

void tickJob() {
  if (job.running) {
    const uint32_t now = micros();
    if (job.batch) tickBatch(now);
    else if (job.held && int32_t(now - job.releaseAt) >= 0) {
      Keyboard.release(job.key);
      job.held = false;
      if (job.sent == job.count) finishJob(0);
    }
    if (job.running && !job.batch && !job.held && int32_t(now - job.next) >= 0) {
      if (!Keyboard.press(job.key)) {
        finishJob(2);
      } else {
        job.held = true;
        ++job.sent;
        job.releaseAt = now + job.hold;
        job.next += job.period;
        if (int32_t(job.releaseAt - job.next) >= 0) job.next = now + job.period;
      }
    }
  }
  if (job.done) {
    uint8_t payload[9] = {};
    put16(payload, job.id);
    payload[2] = job.result;
    put16(payload + 3, job.sent);
    put16(payload + 5, job.sent >> 16);
    put16(payload + 7, job.result == 2 ? UNSUPPORTED : 0);
    if (sendReply(JOB_DONE, 0, session, payload, sizeof(payload))) job = {};
  }
}

bool validBatch(const uint8_t *p, uint8_t count) {
  uint8_t keys[15] = {};
  uint8_t buttons = 0;
  uint32_t previous = 0;
  bool moves = false, clicks = false;
  for (uint8_t i = 0; i < count; ++i, p += BATCH_EDGE_SIZE) {
    const uint32_t at = get32(p);
    const uint8_t kind = p[4], value = p[5];
    if (at < previous || at > 5000000) return false;
    previous = at;
    if (kind == 5) {
      if (value == 128 || p[6] == 128) return false;
      moves = true;
      continue;
    }
    if (p[6] || kind < 1 || kind > 4) return false;
    const bool keyboard = kind <= 2;
    if (keyboard ? value < 4 || value > 0x73 : value < 1 || value > 3) return false;
    if (!keyboard) clicks = true;
    uint8_t &held = keyboard ? keys[value / 8] : buttons;
    const uint8_t mask = 1 << (value % 8);
    const bool down = kind == 1 || kind == 3;
    if (down == bool(held & mask)) return false;
    held ^= mask;
  }
  if (buttons || (moves && clicks)) return false;
  for (uint8_t held : keys) if (held) return false;
  return true;
}

void neutralize() {
  const bool wasArmed = state == ARMED;
  state = DISARMED;
  if (session) closedSession = session;
  session = 0;
  lastSeq = 0;
  lastRequestLength = 0;
  job = {};
  if (wasArmed) {
    Keyboard.releaseAll();
    Mouse.release(MOUSE_ALL);
  }
}

void fault(Error error) {
  const uint32_t token = session;
  lastError = error;
  neutralize();
  const uint8_t payload[] = {error, 0};
  sendReply(FAULT, 0, token, payload, sizeof(payload));
}

void reject(const uint8_t *raw, Error error, bool disarm = false) {
  if (disarm) {
    lastError = error;
    neutralize();
  }
  const uint8_t payload[] = {raw[2], error, 0};
  sendReply(NACK, get16(raw + 4), get32(raw + 6), payload, sizeof(payload));
}

void rememberAndReply(const uint8_t *raw, uint8_t length, Opcode op,
                      const uint8_t *payload, uint8_t size) {
  lastSeq = get16(raw + 4);
  lastRequestLength = length;
  memcpy(lastRequest, raw, length);
  lastReply = op;
  memcpy(lastPayload, payload, size);
  lastPayloadLength = size;
  sendReply(op, lastSeq, session, payload, size);
}

Error applyInput(const uint8_t *p, uint8_t size) {
  if (size == 6 && p[0] == 1) {
    if (p[1] != 1 && p[1] != 2) return BAD_PAYLOAD;
    if (get16(p + 2) != 7) return UNSUPPORTED;
    const uint16_t usage = get16(p + 4);
    uint8_t key;
    if (usage >= 4 && usage <= 0x73) key = usage + 136;
    else if (usage >= 0xE0 && usage <= 0xE7) key = usage - 0xE0 + 128;
    else return UNSUPPORTED;
    if (p[1] == 1 && !Keyboard.press(key)) return UNSUPPORTED;
    if (p[1] == 2) Keyboard.release(key);
  } else if (size == 3 && p[0] == 2) {
    if (p[1] != 1 && p[1] != 2) return BAD_PAYLOAD;
    const uint8_t buttons[] = {MOUSE_LEFT, MOUSE_RIGHT, MOUSE_MIDDLE};
    if (p[2] < 1 || p[2] > sizeof(buttons)) return UNSUPPORTED;
    if (p[1] == 1) Mouse.press(buttons[p[2] - 1]);
    else Mouse.release(buttons[p[2] - 1]);
  } else if (size == 5 && p[0] == 3) {
    const int16_t x = get16(p + 1), y = get16(p + 3);
    if (x < -127 || x > 127 || y < -127 || y > 127) return UNSUPPORTED;
    Mouse.move(x, y, 0);
  } else {
    return BAD_PAYLOAD;
  }
  return static_cast<Error>(0);
}

void handlePacket(const uint8_t *raw, uint8_t length) {
  if (length < HEADER_SIZE + 2 || raw[3] || get16(raw + 10) > MAX_PAYLOAD ||
      HEADER_SIZE + get16(raw + 10) + 2 != length ||
      crc16(raw, length - 2) != get16(raw + length - 2)) {
    fault(INVALID_FRAME);
    return;
  }

  const uint8_t op = raw[2];
  const uint16_t seq = get16(raw + 4);
  const uint32_t token = get32(raw + 6);
  const uint8_t size = raw[10];
  const uint8_t *payload = raw + HEADER_SIZE;
  const bool starting = op == HELLO && state == DISARMED;
  const bool stopping = op == RELEASE_ALL || op == DISARM;
  if (op >= ACK) return;
  if (!starting && (!token || token != (stopping && !session ? closedSession : session))) {
    reject(raw, INVALID_SESSION);
    return;
  }
  if (raw[0] != 1 || raw[1] != 0) {
    reject(raw, INCOMPATIBLE_VERSION, !starting);
    return;
  }

  if (stopping) {
    if (size) {
      reject(raw, BAD_PAYLOAD, true);
      return;
    }
    neutralize();
    sendReply(ACK, seq, token, &op, 1);
    return;
  }
  if (starting) {
    if (size != 6 || payload[0] != 1 || payload[1] != 0 || get32(payload + 2) != token) {
      reject(raw, BAD_PAYLOAD);
      return;
    }
    for (uint32_t recent : recentSessions) {
      if (recent == token) {
        reject(raw, INVALID_SESSION);
        return;
      }
    }
    recentSessions[nextSessionSlot] = token;
    nextSessionSlot = (nextSessionSlot + 1) % 32;
    session = token;
    state = IDLE;
    lastError = 0;
    lastHeartbeat = millis();
    const uint8_t hello[] = {1, 0, 0, 3, 0, IDLE, 'L', 'e', 'o', 'n', 'a', 'r', 'd', 'o'};
    rememberAndReply(raw, length, HELLO_ACK, hello, sizeof(hello));
    return;
  }
  if (seq <= lastSeq) {
    if (length == lastRequestLength && !memcmp(raw, lastRequest, length))
      sendReply(lastReply, seq, token, lastPayload, lastPayloadLength);
    else
      reject(raw, SEQUENCE_CONFLICT);
    return;
  }
  if (op == INPUT_EVENT) {
    if (state != ARMED) {
      reject(raw, INVALID_STATE);
      return;
    }
    if (job.running && (job.batch || (size && payload[0] == 1))) {
      reject(raw, INVALID_STATE, true);
      return;
    }
    if (const Error error = applyInput(payload, size)) {
      reject(raw, error, true);
      return;
    }
  } else if (op == QUEUE_BATCH) {
    if (state != ARMED || job.id) {
      reject(raw, INVALID_STATE);
      return;
    }
    if (size < 3 || !get16(payload) || !payload[2] || payload[2] > BATCH_LIMIT ||
        size != 3 + payload[2] * BATCH_EDGE_SIZE || !validBatch(payload + 3, payload[2])) {
      reject(raw, BAD_PAYLOAD);
      return;
    }
    job.id = get16(payload);
    job.count = payload[2];
    job.batch = true;
    memcpy(batchEdges, payload + 3, size - 3);
    const uint8_t reply[] = {op, payload[0], payload[1]};
    rememberAndReply(raw, length, ACK, reply, sizeof(reply));
    return;
  } else if (op == PREPARE_JOB) {
    if (state != ARMED || job.id) {
      reject(raw, INVALID_STATE);
      return;
    }
    if (size != 18 || !get16(payload) || get16(payload + 2) != 7) {
      reject(raw, BAD_PAYLOAD);
      return;
    }
    const uint16_t usage = get16(payload + 4);
    const uint32_t period = get32(payload + 6), hold = get32(payload + 10), count = get32(payload + 14);
    if (usage < 4 || usage > 0x73 || period < 5000 || period > 1000000 ||
        !hold || hold >= period || !count || count > 1000000) {
      reject(raw, UNSUPPORTED);
      return;
    }
    job.id = get16(payload);
    job.key = usage + 136;
    job.period = period;
    job.hold = hold;
    job.count = count;
    const uint8_t reply[] = {op, payload[0], payload[1]};
    rememberAndReply(raw, length, ACK, reply, sizeof(reply));
    return;
  } else if (op == START_JOB || op == CANCEL_JOB) {
    if (state != ARMED || size != 2 || !get16(payload)) {
      reject(raw, INVALID_STATE);
      return;
    }
    if (op == START_JOB) {
      if (get16(payload) != job.id || job.running || job.done) {
        reject(raw, INVALID_STATE);
        return;
      }
      Keyboard.releaseAll();
      if (job.batch) Mouse.release(MOUSE_ALL);
      job.running = true;
      job.next = micros();
    } else if (job.id) {
      if (get16(payload) != job.id) {
        reject(raw, INVALID_STATE);
        return;
      }
      if (!job.done) finishJob(1);
    }
  } else if (size) {
    reject(raw, BAD_PAYLOAD, true);
    return;
  } else if (op == ARM && state == IDLE) {
    state = ARMED;
    lastHeartbeat = millis();
  } else if (op == HEARTBEAT && state == ARMED) {
    lastHeartbeat = millis();
  } else if (op == GET_CAPS) {
    const uint8_t caps[] = {15, MAX_PAYLOAD, WATCHDOG_MS & 0xFF, WATCHDOG_MS >> 8, 1};
    rememberAndReply(raw, length, CAPS, caps, sizeof(caps));
    return;
  } else if (op == GET_DEVICE_STATUS) {
    const uint8_t status[] = {state, uint8_t(job.id), uint8_t(job.id >> 8), lastError, 0};
    rememberAndReply(raw, length, STATUS, status, sizeof(status));
    return;
  } else {
    reject(raw, INVALID_STATE);
    return;
  }
  rememberAndReply(raw, length, ACK, &op, 1);
}

void setup() {
  Serial.begin(115200);
  Keyboard.begin();
  Mouse.begin();
}

void loop() {
  if (!Serial.dtr()) {
    neutralize();
    encodedLength = 0;
    discarding = false;
    return;
  }
  if (state != DISARMED && uint32_t(millis() - lastHeartbeat) >= WATCHDOG_MS)
    fault(WATCHDOG_EXPIRED);

  for (uint8_t budget = 0; budget < 16 && Serial.available(); ++budget) {
    const int next = Serial.read();
    if (next < 0) break;
    if (next == 0) {
      if (discarding) {
        discarding = false;
      } else if (encodedLength) {
        uint8_t raw[MAX_RAW];
        handlePacket(raw, decodePacket(raw));
      }
      encodedLength = 0;
    } else if (!discarding) {
      if (encodedLength == MAX_ENCODED) {
        discarding = true;
        fault(INVALID_FRAME);
      } else {
        encoded[encodedLength++] = next;
      }
    }
  }
  tickJob();
}
