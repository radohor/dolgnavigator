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

    if (kind === "full_extract") {
      task = `
Ты являешься ОСНОВНЫМ парсером российского кредитного отчета БКИ.
Перед тобой фрагмент отчета с маркерами страниц вида [[PAGE 123]].

Нужно извлечь ВСЕ реальные записи, полностью содержащиеся или начинающиеся в этом фрагменте:
1. Кредитные договоры.
2. Кредитные заявления/обращения, включая отказанные.
3. Запросы кредитной истории.

КРИТИЧЕСКИЕ ПРАВИЛА:
- Не путай договор, заявку и запрос БКИ.
- Не превращай заголовки, итоги, легенды таблиц и служебный текст в записи.
- Если одна запись переносится между строками, собери ее целиком.
- Если название кредитора перенесено на несколько строк, собери полное название, а не "ООО", "АО" или "Общество с ограниченной".
- Для УИД собери ВСЕ части, даже если УИД перенесен между строками/колонками.
- Если запись началась до первой страницы фрагмента и здесь виден только хвост, не создавай ее повторно.
- Если запись начинается на последней странице фрагмента и продолжается дальше, извлеки то, что достоверно видно; неизвестные поля оставь null.
- Ничего не выдумывай.
- source_page — номер страницы из ближайшего маркера [[PAGE N]], где начинается запись.
- evidence — короткая опорная фраза из источника, не более 12 слов.
- confidence: "high", "medium" или "low".
- Верни ТОЛЬКО валидный JSON без markdown.

Формат:
{
  "contracts": [{
    "creditor": "полное название кредитора или null",
    "contract_date": "YYYY-MM-DD или null",
    "amount": 0,
    "currency": "RUB или null",
    "contract_id": "string или null",
    "uid": "полный УИД или null",
    "status": "string или null",
    "product": "string или null",
    "first_overdue_date": "YYYY-MM-DD или null",
    "max_overdue_days": 0,
    "paid_total": 0,
    "actual_end_date": "YYYY-MM-DD или null",
    "termination_basis": "string или null",
    "source_page": 0,
    "confidence": "high|medium|low",
    "evidence": "string"
  }],
  "applications": [{
    "creditor": "полное название кредитора или null",
    "application_date": "YYYY-MM-DD или null",
    "amount": 0,
    "currency": "RUB или null",
    "uid": "полный УИД обращения или null",
    "status": "Отказ|Выдано|Одобрено|На рассмотрении|иное",
    "source_page": 0,
    "confidence": "high|medium|low",
    "evidence": "string"
  }],
  "queries": [{
    "requester": "полное название пользователя кредитной истории или null",
    "query_date": "YYYY-MM-DD или null",
    "purpose": "string или null",
    "amount": 0,
    "source_page": 0,
    "confidence": "high|medium|low",
    "evidence": "string"
  }],
  "warnings": []
}`;
    } else if (kind === "applications") {
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
Это КОНТРОЛЬ КАЧЕСТВА локального парсера. Тебе переданы контрольные фрагменты разных разделов отчета, а также фактическое число структурных маркеров и локально извлечённых записей.
Локальный результат и сигналы:
${JSON.stringify(meta).slice(0,8000)}

Сравни local с signals. Если signals содержит ненулевой structural marker count, считай его надёжным denominator только для этого раздела. Если marker count заметно больше числа локально извлечённых записей, это parser_anomaly. Если marker count равен 0, не делай вывод о полноте этого раздела по marker count. Не называй результат ok при таком числовом расхождении.
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

    const prompt = `${base}\n${task}\n\nФрагмент отчета:\n${text.slice(0,65000)}`;

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
