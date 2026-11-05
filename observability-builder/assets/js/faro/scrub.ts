// beforeSend scrubber: the last step before a Faro item leaves the page. It covers the meta, the
// payload and the attributes Faro copies onto spans (user.*, session.*). It fails closed: an item
// it can't scrub is dropped, never sent raw.
import type { TransportItem } from '@grafana/faro-web-sdk';

const SECRET_KEY = /pass(word|wd)?|secret|token|api[_-]?key|authori[sz]ation|cookie|credential|private[_-]?key|card|cvv|cvc/i;
// Personal data Faro can carry under these names (meta.user, span attributes, event attributes):
// dropped, not masked. user.id and user.hash stay (opaque).
const DROP_KEY = /^(user|enduser)[._](email|name|username|full_?name|phone)$/i;
const USER_DROP = ['email', 'username', 'fullName', 'attributes'] as const;
const EMAIL = /([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})/g;
const BEARER = /\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}/gi;
const JWT = /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g;
const LONG_SECRET = /\b(?=[A-Za-z0-9+/_-]*\d)(?=[A-Za-z0-9+/_-]*[A-Za-z])[A-Za-z0-9+/_-]{32,}={0,2}/g;
// 13-19 digits, optionally grouped by spaces or dashes, not part of a decimal or a longer number.
const CARD = /(?<![\d.])\d(?:[ -]?\d){12,18}(?![\d.])/g;
const URL_IN_TEXT = /\b(https?:\/\/[^\s"'<>]+|\/[^\s"'<>?#]*\?[^\s"'<>]+)/g;
// Identifiers (trace, span, session, page, user ids) look like tokens or card numbers.
const ID_KEY = /(^|[._-])(id|ids|hash)$|traceid|spanid|parentspanid/i;
// Timings, sizes and counts are numbers sent as strings; timestamps and OTLP ids too.
const KEEP_KEY = /unixnano$|^timestamp$|^(traceid|spanid|parentspanid)$|(time|duration|delay|size|count|ms|ns)$/i;
// OTLP AnyValue fields: they inherit the attribute's name.
const OTLP_VALUE = new Set(['value', 'stringValue', 'arrayValue', 'kvlistValue', 'values']);
const MAX_DEPTH = 12;

export interface ScrubOptions {
  allowedQueryParams?: string[];
}

// j***@example.com: the shared masking rule for emails (data-model.md, PII masking).
export const maskEmail = (text: string): string => text.replace(EMAIL, '$1***@$2');

function luhn(digits: string): boolean {
  let sum = 0;
  for (let i = 0; i < digits.length; i++) {
    let d = Number(digits[digits.length - 1 - i]);
    if (i % 2 === 1) d = d * 2 > 9 ? d * 2 - 9 : d * 2;
    sum += d;
  }
  return sum % 10 === 0;
}

export function sanitizeUrl(raw: string, allowed: ReadonlySet<string>): string {
  try {
    const absolute = /^[a-z][a-z0-9+.-]*:/i.test(raw);
    const url = new URL(raw, 'http://relative.invalid');
    url.hash = '';
    url.username = '';
    url.password = '';
    for (const key of [...url.searchParams.keys()]) if (!allowed.has(key)) url.searchParams.delete(key);
    const out = url.toString();
    return absolute ? out : out.replace('http://relative.invalid', '');
  } catch {
    return raw.split(/[?#]/)[0];
  }
}

export function scrubText(text: string, allowed: ReadonlySet<string>, isId = false): string {
  let out = text.replace(URL_IN_TEXT, (u) => sanitizeUrl(u, allowed)).replace(BEARER, '$1 [redacted]').replace(JWT, '[redacted]');
  if (!isId) {
    out = out
      .replace(CARD, (m) => (luhn(m.replace(/\D/g, '')) ? '[redacted]' : m))
      .replace(LONG_SECRET, '[redacted]');
  }
  return maskEmail(out);
}

const DROPPED = Symbol('dropped');

function walk(value: unknown, allowed: ReadonlySet<string>, depth: number, key = ''): unknown {
  if (depth > MAX_DEPTH) return DROPPED;
  if (key && DROP_KEY.test(key)) return DROPPED;
  if (typeof value === 'string') {
    if (SECRET_KEY.test(key)) return '[redacted]';
    if (KEEP_KEY.test(key) && !/[@/?]/.test(value)) return value;
    return scrubText(value, allowed, ID_KEY.test(key));
  }
  if (Array.isArray(value)) return value.map((v) => walk(v, allowed, depth + 1, key)).filter((v) => v !== DROPPED);
  if (value && typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    // OTLP attribute { key: 'user.email', value: { stringValue: … } }: the name decides.
    const attrKey = typeof obj.key === 'string' && 'value' in obj ? obj.key : '';
    if (attrKey && DROP_KEY.test(attrKey)) return DROPPED;
    const inherited = attrKey || key;
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(obj)) {
      const next = walk(v, allowed, depth + 1, OTLP_VALUE.has(k) && inherited ? inherited : k);
      if (next !== DROPPED) out[k] = next;
    }
    return out;
  }
  return value;
}

export function makeBeforeSend(options: ScrubOptions = {}) {
  const allowed = new Set(options.allowedQueryParams ?? []);
  return (item: TransportItem): TransportItem | null => {
    try {
      const user = item.meta.user ? { ...item.meta.user } : undefined;
      if (user) for (const k of USER_DROP) delete user[k];
      const meta = walk(user ? { ...item.meta, user } : item.meta, allowed, 0);
      const payload = walk(item.payload, allowed, 0);
      if (meta === DROPPED || payload === DROPPED) return null;
      return { ...item, meta: meta as TransportItem['meta'], payload: payload as TransportItem['payload'] };
    } catch {
      return null;
    }
  };
}
