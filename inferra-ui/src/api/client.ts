import OpenAI from "openai";
import axios from "axios";

export function makeOpenAIClient(baseURL: string, apiKey: string) {
  return new OpenAI({ baseURL: `${baseURL}/v1`, apiKey, dangerouslyAllowBrowser: true });
}

export function makeAdminClient(baseURL: string, adminKey: string) {
  return axios.create({
    baseURL: `${baseURL}/v1`,
    headers: { Authorization: `Bearer ${adminKey}` },
  });
}

export function makeInferenceClient(baseURL: string, inferenceKey: string) {
  return axios.create({
    baseURL: `${baseURL}/v1`,
    headers: { Authorization: `Bearer ${inferenceKey}` },
  });
}
