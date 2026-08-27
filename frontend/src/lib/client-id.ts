/**
 * Generate an opaque client-side id without assuming a secure browser context.
 *
 * `crypto.randomUUID()` is available only in secure contexts in several
 * browsers. The LAN development deployment is intentionally served via HTTP,
 * so research drafts must remain usable when that API is unavailable.
 */
export function createUuid(): string {
  const browserCrypto = typeof globalThis === "undefined" ? undefined : globalThis.crypto;
  if (typeof browserCrypto?.randomUUID === "function") {
    return browserCrypto.randomUUID();
  }

  const bytes = new Uint8Array(16);
  if (typeof browserCrypto?.getRandomValues === "function") {
    browserCrypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
}

export function createClientId(prefix = "client"): string {
  return `${prefix}-${createUuid()}`;
}
