// Direct Anthropic API client for the lawn-health assessment.
//
// The photo button captures a JPEG from the Pi over BLE and sends it
// straight to the Claude vision endpoint — no server in between. The
// Anthropic API key is entered once in-app and stored in AsyncStorage.
// (Embedding a key in a personal-use device is fine; if this ever ships
// to anyone else, proxy it through the Pi instead.)
//
// Schema + prompt are kept in lock-step with scripts/lawn_camera.py on
// the Pi (the "thick client" path). Same model, same fields, same
// recommendation shape (action + when + priority).

import AsyncStorage from '@react-native-async-storage/async-storage';

const API_KEY_STORAGE = '@robot-control/anthropic-api-key';
const MESSAGES_URL = 'https://api.anthropic.com/v1/messages';
const MODEL = 'claude-opus-4-7';
const API_VERSION = '2023-06-01';

export async function getApiKey(): Promise<string | null> {
  return AsyncStorage.getItem(API_KEY_STORAGE);
}

export async function setApiKey(key: string): Promise<void> {
  await AsyncStorage.setItem(API_KEY_STORAGE, key.trim());
}

export async function clearApiKey(): Promise<void> {
  await AsyncStorage.removeItem(API_KEY_STORAGE);
}

export type HealthStatus =
  | 'healthy' | 'fair' | 'stressed' | 'unhealthy' | 'no_lawn' | 'unknown';

export type Priority = 'high' | 'medium' | 'low';

export interface Recommendation {
  action: string;
  when: string;
  priority: Priority;
}

export interface GrassAssessment {
  // Whether a managed lawn is the subject of the photo.
  lawnPresent: boolean;
  // 0–1 confidence in lawnPresent.
  confidence: number;
  // Bucketed health verdict.
  status: HealthStatus;
  // 0–100 health score, or null when no lawn present.
  score: number | null;
  // One-paragraph natural-language summary.
  summary: string;
  // Short tags for each visible problem.
  issues: string[];
  // Concrete actions WITH when + priority.
  recommendations: Recommendation[];
  // Raw assistant text for debugging.
  raw: string;
}

// Kept identical in spirit to scripts/lawn_camera.py SYSTEM_PROMPT so
// desktop + iOS land on the same recommendations / scoring.
const SYSTEM_PROMPT = `You are a turf-care assistant. You receive a single photo taken by a camera mounted on a lawn-care robot and must report on it in a structured way.

Step 1 — Is there a lawn? A "lawn" is an area of managed/mown turf grass (home lawn, park, sports field, etc.). Patchy grass on dirt, ornamental grasses, crops, hay fields, indoor scenes, pavement, gravel, walls, the sky, or close-ups of the robot itself are NOT lawns. If you are not reasonably sure a lawn is the subject of the photo, set lawn_present to false and treat the rest of the assessment as "no lawn".

Step 2 — If a lawn IS present, assess its health from what is visible:
  - Color & uniformity: deep, even green is healthy; yellowing, browning, or patchy color signals stress (drought, dormancy, nutrient deficiency, disease).
  - Density & coverage: thick, full turf is healthy; thin spots, bare soil, or visible thatch are problems.
  - Weeds & invaders: clover, dandelions, crabgrass, moss, broadleaf weeds.
  - Mowing/condition: scalped areas, ruts, overgrown/leggy growth, debris.
  - Disease/pest signs: rings, irregular dead patches, fungal mats.
  Map your overall judgement to health_status and to a 0–100 health_score (0 = dead/bare, 100 = lush, dense, uniform, weed-free). If no lawn is present, use health_status "no_lawn" and health_score 0.

Be specific and concise. Only report issues and recommendations you can actually justify from the image — do not speculate beyond what is visible. Recommendations must be concrete turf-care actions (water, mow at X height, overseed, fertilise, spot-treat weeds, dethatch, etc.).

For EACH recommendation, also say WHEN to do it and how URGENT it is:
  - when: a specific schedule or cadence — e.g. "twice weekly until rain returns", "next mow, within 3–5 days, dry grass only", "now, then again in 4–6 weeks", "early autumn (Sept–Oct)". Never use vague phrasing like "as needed" or "regularly" — give a concrete window or frequency the operator can act on.
  - priority: "high" (do this week), "medium" (do within a month), or "low" (seasonal / routine maintenance).`;

const USER_PROMPT = `Assess this photo: is a lawn present, and if so, how healthy is it? Respond as compact JSON, no markdown, no prose outside the JSON:

{
  "lawn_present": <bool>,
  "confidence": <0.0–1.0>,
  "health_status": "healthy" | "fair" | "stressed" | "unhealthy" | "no_lawn" | "unknown",
  "health_score": <integer 0–100, or 0 when no lawn>,
  "summary": "<one or two plain-language sentences>",
  "issues": ["<short tag>", ...],
  "recommendations": [
    {
      "action": "<concrete turf-care action>",
      "when": "<specific schedule or cadence>",
      "priority": "high" | "medium" | "low"
    }
  ]
}`;

export async function assessGrassHealth(jpegBase64: string): Promise<GrassAssessment> {
  const apiKey = await getApiKey();
  if (!apiKey) {
    throw new Error('No Anthropic API key set. Add one in Settings.');
  }

  const body = {
    model: MODEL,
    max_tokens: 2048,
    system: SYSTEM_PROMPT,
    messages: [
      {
        role: 'user',
        content: [
          {
            type: 'image',
            source: { type: 'base64', media_type: 'image/jpeg', data: jpegBase64 },
          },
          { type: 'text', text: USER_PROMPT },
        ],
      },
    ],
  };

  const res = await fetch(MESSAGES_URL, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': API_VERSION,
      'anthropic-dangerous-direct-browser-access': 'true',
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`Claude API ${res.status}: ${text.slice(0, 200)}`);
  }

  const json = await res.json();
  const text: string = (json?.content || [])
    .filter((b: any) => b?.type === 'text')
    .map((b: any) => b.text)
    .join('\n')
    .trim();

  return parseAssessment(text);
}

const VALID_STATUSES: HealthStatus[] = ['healthy', 'fair', 'stressed', 'unhealthy', 'no_lawn', 'unknown'];
const VALID_PRIORITIES: Priority[] = ['high', 'medium', 'low'];

function parseAssessment(text: string): GrassAssessment {
  const fallback: GrassAssessment = {
    lawnPresent: false,
    confidence: 0,
    status: 'unknown',
    score: null,
    summary: text || '(no response)',
    issues: [],
    recommendations: [],
    raw: text,
  };

  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start < 0 || end <= start) return fallback;

  let obj: any;
  try {
    obj = JSON.parse(text.slice(start, end + 1));
  } catch {
    return fallback;
  }

  const lawnPresent = obj.lawn_present === true;
  const confidence = clampNum(obj.confidence, 0, 1);
  const status: HealthStatus = VALID_STATUSES.includes(obj.health_status)
    ? obj.health_status
    : (lawnPresent ? 'unknown' : 'no_lawn');
  const rawScore = typeof obj.health_score === 'number' ? obj.health_score : null;
  const score = lawnPresent && rawScore != null ? clampNum(rawScore, 0, 100) : null;
  const summary = typeof obj.summary === 'string' ? obj.summary : text;
  const issues = Array.isArray(obj.issues)
    ? obj.issues.filter((s: any) => typeof s === 'string' && s.trim().length > 0)
    : [];
  const recommendations: Recommendation[] = Array.isArray(obj.recommendations)
    ? obj.recommendations.map(coerceRec).filter((r: Recommendation | null): r is Recommendation => r !== null)
    : [];

  // Sort high-priority items first so the user sees urgency-ordered output.
  const order: Record<Priority, number> = { high: 0, medium: 1, low: 2 };
  recommendations.sort((a, b) => order[a.priority] - order[b.priority]);

  return { lawnPresent, confidence, status, score, summary, issues, recommendations, raw: text };
}

function coerceRec(r: any): Recommendation | null {
  // Tolerate the legacy plain-string form, in case the API returns the
  // older schema (or a stub during testing).
  if (typeof r === 'string') {
    return { action: r, when: '', priority: 'medium' };
  }
  if (r && typeof r === 'object') {
    const action = typeof r.action === 'string' ? r.action : '';
    if (!action.trim()) return null;
    const when = typeof r.when === 'string' ? r.when : '';
    const priority: Priority = VALID_PRIORITIES.includes(r.priority) ? r.priority : 'medium';
    return { action, when, priority };
  }
  return null;
}

function clampNum(v: any, lo: number, hi: number): number {
  const n = typeof v === 'number' ? v : Number(v);
  if (!isFinite(n)) return lo;
  return Math.max(lo, Math.min(hi, n));
}
