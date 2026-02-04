
import { GoogleGenAI, Type } from "@google/genai";
import { Violation, AIAnalysis } from "../types";

export const analyzeBuildingData = async (violations: Violation[]): Promise<AIAnalysis> => {
  // Use the correct initialization pattern with process.env.API_KEY
  const ai = new GoogleGenAI({ apiKey: process.env.API_KEY });
  
  const prompt = `
    Analyze the following NYC HPD building violations and provide a real estate investment risk assessment.
    Violations: ${JSON.stringify(violations)}
    
    Consider:
    1. Severity of Class C violations (Heat, Lead Paint, Structural).
    2. Recurrence of issues.
    3. Potential cost of remediation.
    4. Likelihood of owner distress.
  `;

  const response = await ai.models.generateContent({
    model: "gemini-3-flash-preview",
    contents: prompt,
    config: {
      responseMimeType: "application/json",
      responseSchema: {
        type: Type.OBJECT,
        properties: {
          summary: { type: Type.STRING, description: "Executive summary of building condition" },
          riskLevel: { type: Type.STRING, enum: ["Low", "Medium", "High", "Extreme"] },
          investmentThesis: { type: Type.STRING, description: "Why this might be a good or bad lead" },
          suggestedAction: { type: Type.STRING, description: "Next step for the investor" },
        },
        required: ["summary", "riskLevel", "investmentThesis", "suggestedAction"]
      }
    }
  });

  // Access the text property directly on the response object
  const jsonStr = response.text || "{}";
  return JSON.parse(jsonStr.trim()) as AIAnalysis;
};
