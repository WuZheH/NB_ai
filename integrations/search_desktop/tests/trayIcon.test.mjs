import assert from "node:assert/strict";
import test from "node:test";
import { createTrayIcon } from "../electron/tray/createTray.js";

const DESIGN_TOKENS = Object.freeze({ primary: "#55A7F7" });

test("tray uses the packaged ICO when Electron can load it", () => {
  const packagedIcon = { isEmpty: () => false };
  let fallbackCalls = 0;
  const nativeImage = {
    createFromPath(path) {
      assert.equal(path, "D:\\Search\\assets\\search.ico");
      return packagedIcon;
    },
    createFromDataURL() {
      fallbackCalls += 1;
      return { fallback: true };
    },
  };

  assert.equal(
    createTrayIcon(nativeImage, "D:\\Search\\assets\\search.ico", DESIGN_TOKENS),
    packagedIcon,
  );
  assert.equal(fallbackCalls, 0);
});

test("tray falls back to the generated Search icon when the ICO is unavailable", () => {
  const fallbackIcon = { fallback: true };
  const nativeImage = {
    createFromPath() {
      return { isEmpty: () => true };
    },
    createFromDataURL(value) {
      assert.match(value, /^data:image\/svg\+xml/);
      assert.match(decodeURIComponent(value), /#55A7F7/);
      return fallbackIcon;
    },
  };

  assert.equal(createTrayIcon(nativeImage, "missing.ico", DESIGN_TOKENS), fallbackIcon);
});

test("tray falls back safely when native icon loading throws", () => {
  const fallbackIcon = { fallback: true };
  const nativeImage = {
    createFromPath() {
      throw new Error("icon_load_failed");
    },
    createFromDataURL() {
      return fallbackIcon;
    },
  };

  assert.equal(createTrayIcon(nativeImage, "broken.ico", DESIGN_TOKENS), fallbackIcon);
});
