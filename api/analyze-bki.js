export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "POST") return res.status(405).json({ error: "Method not allowed" });

  try {
    const apiKey = process.env.OPENAI_API_KEY;
    if (!apiKey) return res.status(500).json({ error: "OPENAI_API_KEY is not configured" });

    const { bureau, text, kind = "contracts", meta = {} } = req.body || {};
    if (!text || typeof text !== "string") {
      return res.status(400).json({ error: "Field 'text' is required" });
    }

    const base = `
Ты проверяешь фрагмент российского кредитного отчета БКИ.
Бюро: ${bureau || "не определено"}.
Ничего не выдумывай. Используй только явно присутствующие в тексте данные.
Если значение отсутствует — null. Не превращай заголовки таблиц, служебный текст и примеры в реальные записи.
`;

    let task = "";

    if (kind === "applications") {
      task = `
Найди только реальные кредитные обращения/заявки в этом фрагменте.
Не считай запрос кредитной истории заявкой.
Не считай договор заявкой, если нет отдельной записи об обращении.
Верни ТОЛЬКО JSON:
{
  "applications": [{
    "creditor": "string",
    "application_date": "YYYY-MM-DD или null",
    "amount": 0,
    "currency": "RUB или null",
    "uid": "string или null",
    "status": "Отказ/Выдано/Одобрено/иное или null"
  }],
  "warnings": []
}`;
    } else if (kind === "queries") {
      task = `
Найди только реальные запросы кредитной истории/обращения пользователей БКИ в этом фрагменте.
Не считай кредитную заявку запросом БКИ.
Верни ТОЛЬКО JSON:
{
  "queries": [{
    "requester": "string",
    "query_date": "YYYY-MM-DD или null",
    "purpose": "string или null",
    "amount": 0
  }],
  "warnings": []
}`;
    } else if (kind === "quality") {
      task = `
Это КОНТРОЛЬ КАЧЕСТВА локального парсера, а не полный повторный разбор отчета.
Локальный результат и сигналы:
${JSON.stringify(meta).slice(0,8000)}

Проверь, противоречат ли видимые в этом фрагменте заголовки/сводные показатели/структура отчета локальным результатам.
Не делай вывод по невидимой части файла.
Верни ТОЛЬКО JSON:
{
  "verdict": "ok|warning|parser_anomaly",
  "warnings": ["конкретное наблюдение"],
  "observed": {
    "contracts_hint": 0,
    "applications_hint": 0,
    "queries_hint": 0
  }
}`;
    } else {
      task = `
Найди только реальные кредитные договоры.
Не считай заявки, отказы и запросы кредитной истории договорами.
Для каждого договора извлеки:
creditor, contract_date, amount, currency, contract_id, uid, status,
first_overdue_date, paid_total, actual_end_date, product.
Верни ТОЛЬКО JSON:
{
  "contracts": [{
    "creditor": "string",
    "contract_date": "YYYY-MM-DD или null",
    "amount": 0,
    "currency": "RUB или null",
    "contract_id": "string или null",
    "uid": "string или null",
    "status": "string или null",
    "first_overdue_date": "YYYY-MM-DD или null",
    "paid_total": 0,
    "actual_end_date": "YYYY-MM-DD или null",
    "product": "string или null"
  }],
  "warnings": []
}`;
    }

    const prompt = `${base}\n${task}\n\nФрагмент отчета:\n${text.slice(0,50000)}`;

    const response = await fetch("https://api.openai.com/v1/responses", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${apiKey}`
      },
      body: JSON.stringify({
        model: "gpt-5.6-luna",
        input: prompt
      })
    });

    const data = await response.json();

    if (!response.ok) {
      console.error("OpenAI API error:", data);
      return res.status(response.status).json({ error: "OpenAI API error", details: data });
    }

    const outputText =
      data.output?.flatMap(item => item.content || [])
        ?.find(item => item.type === "output_text")?.text || "";

    let parsed;
    try {
      parsed = JSON.parse(
        outputText.replace(/^```json\s*/i,"").replace(/^```\s*/i,"").replace(/```$/i,"").trim()
      );
    } catch {
      return res.status(502).json({ error: "AI returned invalid JSON", raw: outputText });
    }

    return res.status(200).json({
      ok: true,
      bureau: bureau || null,
      kind,
      result: parsed
    });
  } catch (error) {
    console.error("Internal server error:", error);
    return res.status(500).json({ error: "Internal server error", message: error?.message || String(error) });
  }
}
