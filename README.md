# Reos Lite: open-source control

Cube26 / iota shut down and pulled the **Reos Lite** app from the Play Store. Without it, the Bluetooth smart lights (`LITE`, `LITE1100` and `LITESTRIP`) can't be controlled.
This repo has the protocol, reverse engineered from the last APK (v2.8), and two replacement controllers:

* **`web/index.html`**: a single-file Web Bluetooth app. Open it in Chrome or Edge on Android, Windows, macOS, ChromeOS or Linux and tap **Add light**.
  Web Bluetooth only works from `https://` or `localhost`, so host the file somewhere (GitHub Pages works) or run `python3 -m http.server -d web` and open `http://localhost:8000`.
* **`reoslite/`**: a Python library and command-line tool built on [bleak](https://github.com/hbldh/bleak). Works on Linux, macOS, Windows and Raspberry Pi, and is handy for automation or a Home Assistant bridge.

The full protocol is in **[docs/PROTOCOL.md](docs/PROTOCOL.md)**.

## CLI

```sh
pip install .            # or: pip install bleak && python -m reoslite ...
reoslite scan
reoslite on                         # first light found; add --all for every light
reoslite -a AA:BB:CC:DD:EE:FF color "#ff8000" -b 120
reoslite color warm
reoslite off
reoslite effect candle              # candle | strobe | kaleidoscope | stop
reoslite flash red
reoslite power-on last              # after a power cut: last colour | white
reoslite timer off 30m              # or: timer on 07:00 -c warm | timer cancel
reoslite schedule add on 06:45 -d mo,tu,we,th,fr -c warm
reoslite schedule list
reoslite schedule disable 1
reoslite info                       # firmware version + serial
reoslite -v raw 03 ff 00 00 c8 00 00 01 00 00 00   # send any command, show the bytes
```

Brightness runs from 0 to 200 (the firmware's full scale).

## Library

```python
import asyncio
from reoslite import ReosLite, scan, protocol as p

async def main():
    dev = (await scan())[0]
    async with ReosLite(dev.address) as lite:
        await lite.on()
        await lite.color(255, 60, 0, brightness=150)
        await lite.effect(p.Effect.CANDLE)
        print(await lite.schedules())

asyncio.run(main())
```

`reoslite.protocol` is pure Python with no Bluetooth dependency, so you can reuse the packet builders with any BLE stack (ESPHome, Node, etc.).

## Tests

```sh
python -m unittest discover -s tests
```

The tests check that every packet builder produces exactly the bytes the original app sends. Each expected value is a literal copy of the app's `new byte[]{…}`.

## Notes and caveats

* A light accepts one BLE connection at a time. Close any other phone or app that is connected to it.
* I haven't tested this against real hardware. The byte layouts are taken directly from the app, but some fields have unknown meanings, and the schedule delete behaves oddly. Both are flagged in PROTOCOL.md. Use `-v` to see the raw traffic.
* Firmware upgrade (OTA) is documented but deliberately not implemented, because it could brick a light.
* This is an interoperability project for people who own these lights. It contains no code or assets from the original app.
