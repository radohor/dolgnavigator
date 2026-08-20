export default async function handler(req, res) {
  // Разрешаем запросы из браузера / Telegram Mini App
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  // Предварительный CORS-запрос браузера
  if (req.method === "OPTIONS") {
    return res.status(204).end();
  }

  // Сам API должен принимать только POST
  if (req.method !== "POST") {
    return res.status(405).json({
      error: "Method not allowed"
    });
  }

  try {
    const apiKey = process.env.OPENAI_API_KEY;

    if (!apiKey) {
      return res.status(500).json({
        error: "OPENAI_API_KEY is not configured"
      });
    }

    const { bureau, text } = req.body || {};

    if (!text || typeof text !== "string") {
      return res.status(400).json({
        error: "Field 'text' is required"
      });
    }

    const prompt = `
Ты анализируешь фрагмент российского кредитного отчета БКИ.

Бюро: ${bureau || "не определено"}

Задача:

1. Найди только реальные кредитные договоры.
2. Не считай заголовки страниц, номера разделов и служебный текст кредиторами.
3. Не путай заявку на кредит с фактически заключенным договором.
4. Не путай отказ в выдаче кредита с договором.
5. Если в одном фрагменте несколько договоров — верни каждый отдельно.
6. Ничего не выдумывай.
7. Если значение поля отсутствует в тексте — верни null.

Для каждого договора извлеки:

- creditor
- contract_date
- amount
- currency
- contract_id
- uid
- status
- first_overdue_date
- paid_total

Верни ТОЛЬКО валидный JSON без пояснений и markdown.

Формат:

{
  "contracts": [
    {
      "creditor": "string",
      "contract_date": "YYYY-MM-DD или null",
      "amount": 0,
      "currency": "RUB",
      "contract_id": "string или null",
      "uid": "string или null",
      "status": "string или null",
      "first_overdue_date": "YYYY-MM-DD или null",
      "paid_total": 0
    }
  ],
  "warnings": []
}

Фрагмент отчета:

${text.slice(0, 50000)}
`;

    const response = await fetch(
      "https://api.openai.com/v1/responses",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${apiKey}`
        },
        body: JSON.stringify({
          model: "gpt-5.6-luna",
          input: prompt
        })
      }
    );

    const data = await response.json();

    if (!response.ok) {
      console.error("OpenAI API error:", data);

      return res.status(response.status).json({
        error: "OpenAI API error",
        details: data
      });
    }

    const outputText =
      data.output
        ?.flatMap(item => item.content || [])
        ?.find(item => item.type === "output_text")
        ?.text || "";

    let parsed;

    try {
      const cleaned = outputText
        .replace(/^```json\s*/i, "")
        .replace(/^```\s*/i, "")
        .replace(/```$/i, "")
        .trim();

      parsed = JSON.parse(cleaned);
    } catch (error) {
      console.error("Invalid AI JSON:", outputText);

      return res.status(502).json({
        error: "AI returned invalid JSON",
        raw: outputText
      });
    }

    if (!parsed || !Array.isArray(parsed.contracts)) {
      parsed = {
        contracts: [],
        warnings: ["AI response did not contain contracts array"]
      };
    }

    return res.status(200).json({
      ok: true,
      bureau: bureau || null,
      result: parsed
    });

  } catch (error) {
    console.error("Internal server error:", error);

    return res.status(500).json({
      error: "Internal server error",
      message: error?.message || String(error)
    });
  }
}
