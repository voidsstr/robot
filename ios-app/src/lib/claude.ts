// Direct Anthropic API client for the lawn-health assessment.
//
// The photo button captures a JPEG from the Pi over BLE and sends it
// straight to the Claude vision endpoint — no server in between. The
// Anthropic API key is entered once in-app and stored in AsyncStorage.
// (Embedding a key in a personal-use device is fine; if this ever ships
// to anyone else, proxy it through the Pi instead.)

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

export interface GrassAssessment {
  // One-paragraph natural-language verdict suitable for direct display.
  summary: string;
  // 0–100 score the model assigns to overall lawn health.
  score: number | null;
  // Raw assistant text, in case the caller wants to display extra detail.
  raw: string;
}

const PROMPT = `You are a lawn-care assistant. The photo was taken from a ground-level robot camera pointed at a lawn. Estimate the health of the grass visible in the frame as a single percentage from 0 to 100, where 100 is a perfectly healthy, uniformly green, well-watered lawn with no weeds, no bare spots, and no discoloration, and 0 is dead/bare/heavily diseased.

Bucket guidance (use these as anchors, but pick any integer in the range that best fits):
  0–30   red zone:    mostly dead, bare, heavily discolored, severe disease/pest damage, or dominated by weeds
  31–75  yellow zone: stressed — patchy color, dry spots, thinning, moderate weeds, drought stress, mild disease
  76–100 green zone:  healthy — even color, full coverage, minimal weeds, no obvious stress

Respond as compact JSON, no markdown, no prose outside the JSON:
{
  "score": <integer 0-100>,
  "summary": "<one short paragraph, 1-3 sentences, plain English; mention the dominant signals you used>",
  "issues": ["<short tag>", ...]
}

If the photo doesn't show any grass at all (e.g. it's pointing at a wall or pavement), return score: null and explain in summary.`;

export async function assessGrassHealth(jpegBase64: string): Promise<GrassAssessment> {
  const apiKey = await getApiKey();
  if (!apiKey) {
    throw new Error('No Anthropic API key set. Add one in Settings.');
  }

  const body = {
    model: MODEL,
    max_tokens: 400,
    messages: [
      {
        role: 'user',
        content: [
          {
            type: 'image',
            source: { type: 'base64', media_type: 'image/jpeg', data: jpegBase64 },
          },
          { type: 'text', text: PROMPT },
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
      // Required when calling the API from a browser/RN context.
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

function parseAssessment(text: string): GrassAssessment {
  // Try to pull out the JSON object even if the model added stray prose.
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start >= 0 && end > start) {
    try {
      const obj = JSON.parse(text.slice(start, end + 1));
      const score = typeof obj.score === 'number' ? obj.score : null;
      const summary = typeof obj.summary === 'string' ? obj.summary : text;
      return { score, summary, raw: text };
    } catch {
      // fall through
    }
  }
  return { score: null, summary: text || '(no response)', raw: text };
}
