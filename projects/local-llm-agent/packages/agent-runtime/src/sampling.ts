// office-tools からの sampling 要求を Gateway に中継する（認証は Runtime が持つ）。
//   purpose=invoice_extract → /v1/steps（response_format でスキーマを強制）
//   purpose=ocr             → /v1/vision/ocr（画像1枚）
//   purpose=image_describe  → /v1/steps（画像つき。DOC-07 の画像の説明。モデルが画像を読めること）
import type { ChatMessage, GatewayClient } from "./gateway/client.js";
import { IMAGE_DESCRIBE_PROMPT } from "./prompt/templates.js";
import type { SamplingHandler } from "./tools/mcp.js";
import { sha256, stableStringify } from "./util.js";

export function createSamplingHandler(gateway: GatewayClient, model: () => string): SamplingHandler {
  return async (req) => {
    if (req.purpose === "ocr") {
      const img = req.messages.flatMap((m) => (Array.isArray(m.content) ? m.content : [m.content])).find((c) => c.type === "image") as
        | { type: "image"; data: string; mimeType: string }
        | undefined;
      if (!img) throw new Error("OCR の画像がありません");
      const r = await gateway.ocr(Buffer.from(img.data, "base64"));
      return { text: JSON.stringify({ text: r.text, confidence: r.confidence }), model: `ocr:${r.engine_used}` };
    }
    if (req.purpose === "image_describe") {
      const img = req.messages.flatMap((m) => (Array.isArray(m.content) ? m.content : [m.content])).find((c) => c.type === "image") as
        | { type: "image"; data: string; mimeType: string }
        | undefined;
      if (!img) throw new Error("説明する画像がありません");
      const messages: ChatMessage[] = [
        {
          role: "user",
          content: [
            { type: "text", text: IMAGE_DESCRIBE_PROMPT },
            { type: "image_url", image_url: { url: `data:${img.mimeType};base64,${img.data}` } },
          ],
        },
      ];
      const key = `sampling:${sha256(stableStringify({ purpose: "image_describe", data: sha256(img.data) }))}`;
      const r = await gateway.infer(key, { model: model(), messages, max_tokens: req.maxTokens, temperature: 0, purpose: "image_describe" });
      return { text: r.output, model: model() };
    }
    const messages = req.messages.map((m) => {
      const parts = Array.isArray(m.content) ? m.content : [m.content];
      const text = parts.map((c) => (c.type === "text" ? c.text : "")).join("\n");
      return { role: m.role === "assistant" ? ("assistant" as const) : ("user" as const), content: text };
    });
    if (req.systemPrompt) messages.unshift({ role: "user", content: req.systemPrompt });
    const purpose = req.purpose === "invoice_extract" ? "invoice_extract" : "agent";
    const key = `sampling:${sha256(stableStringify({ purpose, messages, schema: req.responseSchema ?? null }))}`;
    const r = await gateway.infer(key, {
      model: model(),
      messages,
      max_tokens: req.maxTokens,
      temperature: 0,
      purpose,
      ...(req.responseSchema ? { response_format: { type: "json_schema", schema: req.responseSchema } } : {}),
    });
    return { text: r.output, model: model() };
  };
}
