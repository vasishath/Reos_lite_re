# Reos Lite BLE protocol

I worked this out by reverse engineering **Reos Lite 2.8** (`com.cube26.iota.smartlite`, by Cube26 / iota).
The key classes in the decompiled app are listed here so you can check the findings yourself:

| Class (obfuscated) | Role |
|---|---|
| `AutoScanConnectService` | BLE scan, filter by device name |
| `BluetoothLeService` | GATT connection, callbacks, notification dispatch |
| `util.a` | Characteristic discovery, serial/version handling, firmware OTA |
| `util.g` | **Every command packet is built here** |
| `d.a` | Per-light state (holds the GATT characteristics) |
| `c.f`, `Apps.SetScheduleActivity` | Schedule list/parse and create/update |

## 1. Discovery

* The lights advertise their name as **`LITE`**, **`LITE1100`** or **`LITESTRIP`**. The app compares names case-insensitively, and the firmware images contain `Lite` / `Lite1100`.
* The app filters on the name alone. It does not look at service UUIDs or manufacturer data.
* The app allows up to 7 lights at once.
* The radio is a **TI CC254x** (8051 firmware based on the "SimpleBLEPeripheral" sample: the `Characteristic 1..6` strings are still in the image). The `LITE1100` firmware also contains the string `FEIBIT Electronics`.

## 2. GATT layout

All of these are 16-bit UUIDs on the Bluetooth base UUID (`0000xxxx-0000-1000-8000-00805f9b34fb`).
The app finds them by iterating every service and matching the 16-bit part. In practice they sit in service **0xFFF0**.

| UUID | App field | Use |
|---|---|---|
| `0x2A00` | `f3477a` | Device Name (read) |
| `0xFFF1` | `f3479c` | **Info**: read it to get the firmware version (bytes 11 and 12). Firmware-upgrade data is also written here, without response. |
| `0xFFF3` | `f3478b` | **Command**: every command packet below is written here |
| `0xFFF4` | `f3480d` | **Notify**: replies from the light |

Connection sequence used by the app:
connect → discover services → enable notify on FFF4 → send `READ_SERIAL` → read FFF1 (version) → `SYNC_TIME` → optionally `POWER_ON_BEHAVIOR`.

## 3. Framing

```
0F  LEN  CMD  00  PAYLOAD...  CHK  FF FF
```

* `LEN` is the number of bytes from `CMD` through `CHK` inclusive, i.e. the total length minus 4.
* `CHK = (1 + CMD + 00 + sum(PAYLOAD)) & 0xFF`
* Writes longer than 20 bytes are split into 20-byte chunks, about 50 ms apart. A 23-byte schedule packet goes out as 20 + 3 bytes. The light reassembles them using `LEN`.
* Replies on FFF4 use the same framing and can also arrive in several notifications.

Example (read serial): `0F 05 11 00 00 00 12 FF FF`

## 4. Commands (phone → light, written to FFF3)

Colour values are bytes 0–255. **Brightness runs from 0 to 200 (`0xC8`)**. The app's slider steps are 15, 25, 50, 75, 100, 125, 140, 165, 185, 195 and 200.

### 0x01 SYNC_TIME
`sec min hour day month yearHi yearLo 00 00`
The app sends this before every timer or schedule command and after connecting.

### 0x03 SET_COLOR (static colour)
`R G B BRI f1 f2 f3 00 00 00`
* Colour wheel and preset swatches send `f1 f2 f3 = 00 00 01`, and pass R, G and B through the gamma table first (see §6).
* The SMS alert path sends `00 00 00`.
* Two other combinations show up: `64 01 01` when the colour is white and the colour-mode index is 5 or 7, and `n 01 01` from a helper the app never calls. Their meaning is unknown.

### 0x05 POWER_ON_BEHAVIOR
`00 01 X 01`: `X = 0` → come on with the **last colour** when power is applied, `X = 1` → come on **white**.

### 0x09 TIMER (one-shot)
`ACTION sec min hour day month yearHi yearLo R G B BRI`
* `ACTION`: `1` = turn on, `2` = turn off, `0` = cancel.
* The time is absolute, in the light's clock, so send SYNC_TIME first.
* The colour is the one the light should come on with. The app sends the last colour and brightness 200.
* The app also has a shorter variant without `R G B BRI`.

### 0x0A CHECK
`00 00`. Defined in the app but no screen uses it.

### 0x0B FACTORY_RESET
`00 00`. Sent by "Reset" in the light settings.

### 0x0C FLASH (notification alert)
`MODE 00 COUNT R G B BRI 00 00` (R, G and B gamma-corrected)
* `MODE 1, COUNT 1`: "Single Flash Alert"
* `MODE 5, COUNT 1`: "Flash Alert". The app resends this 8 times, 400 ms apart, for incoming calls.
* `MODE 5, COUNT 5`: preview in the alert drawer

### 0x0D FADE (smooth on/off/transition)
`R G B BRI SPEED 00 00`
* Off: `00 00 00 00 05 00 00`
* On: `R G B BRI 05 00 00`, using the last colour
* After an alert the app restores the colour with `SPEED = 2`.

### 0x11 READ_SERIAL
`00 00`. The reply has the same command byte (see §5).

### 0x12 EFFECT (runs in the firmware)
`EFFECT 02 R G B BRI 00 00`

| EFFECT | Name | App sends |
|---|---|---|
| 0 | stop / cancel effect | zeros |
| 2 | Kaleidoscope | zeros |
| 3 | Strobe | `FF FF FF C8` (strobe colour and brightness) |
| 4 | Candle flicker | zeros |

The app's "music sync", "shake" and similar modes run on the phone: they just stream SET_COLOR packets. Strobe gets the same treatment on firmware 1.01/1.02/1.04 and on LITESTRIP.

### 0x13 SCHEDULE_WRITE (repeating rules stored in the light, max 4)
```
OP ID EN ON DAYS YY MM DD HH MI 00 00 R G B BRI
```
* `OP`: `0` = add (send `ID = 0`; the light assigns one), `1` = update `ID`, `2` = delete.
* `EN`: rule enabled (the toggle in the schedule list).
* `ON`: `1` = turn on with R G B BRI, `0` = turn off (the app zeroes the colour and brightness).
* `DAYS`: bitmask with bit0 = Sunday, bit1 = Monday, … bit6 = Saturday.
* `YY MM DD`: the date the rule was created (year − 2000).
* `HH MI`: trigger time.
* This is 23 bytes on the wire, so it goes out as 20 + 3.
* **Delete caveat:** the app's delete action always sends `02 00 01 01 00…00 C8`, i.e. `ID = 0`, no matter which rule you picked. Either the firmware treats it as "delete all", or the app had a bug. The tools here let you pass an ID.

### 0x14 SCHEDULE_READ
`00 00 00` → the reply lists the rules (see §5).

### 0xFF FIRMWARE (OTA): not implemented in the tools
1. Write `0F 06 FF 00 00 00 00 00 FF FF` to FFF3. The light replies `FF … [4]=0`.
2. Write `0F 06 FF 00 01 00 00 01 FF FF`. The light replies `[4]=1`, meaning ready.
3. Stream the image to **FFF1** in 20-byte writes without response. Images are bundled in the APK: `cube26_led.hex` for `LITE`, `cube_V1.02_1100.hex` for `LITE1100`. Despite the names they are raw binaries.
4. Write `0F 06 FF 00 02 SH SL CK FF FF`, where `S = (sum of all image bytes + 1)` as 16 bits. The light replies `[4]=2` (success) or `[4]=3` (failure).

## 5. Notifications (light → phone, FFF4)

| CMD | Payload | Meaning |
|---|---|---|
| 0x04 | `R G B …` | Current colour report. The app updates its UI with it. |
| 0x11 | 16 ASCII bytes | Serial number. The app reads `new String(pkt, 4, 16)` from the first notification. |
| 0x14 | `COUNT` + `COUNT × 15` bytes | Schedule list. Each record is `ID EN ON DAYS YY MM DD HH MI ?? ?? R G B BRI`. Total length is `15·COUNT + 8`. |
| 0xFF | `[4]` = stage | Firmware upgrade handshake (see above) |

## 6. Gamma table

The colour wheel, presets and FLASH command map each channel through this table before sending (`util.g.Y`).
It is reproduced in `reoslite/protocol.py` as `GAMMA` and in `web/index.html`.
The FADE command (on/off) sends raw values.

## 7. Cloud and analytics (not needed for control)

After connecting, the app posted `{activationTime, email (first Google account), firmwareVersion, macAddress, serialNumber}` to a Cube26 backend at `…/save_lite_activation/`.
The base URL comes from an encrypted asset, `com.cube26.iota.smartliteConfig.json`.
The app also bundled Flurry and Crashlytics.
Nothing on the cloud side is needed to control the lights: everything above is local BLE.
