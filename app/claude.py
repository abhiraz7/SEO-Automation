import os
import anthropic

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def generate_suggestions(issue_category: str, issue_message: str, page_context: dict) -> list[str]:
    prompt = f"""You are an SEO expert. Generate exactly 5 distinct suggestions to fix this SEO issue.

Issue category: {issue_category}
Issue: {issue_message}
Page URL: {page_context.get('url', '')}
Current value: {page_context.get('current_value', 'N/A')}

Return ONLY a numbered list 1-5. Each suggestion is a ready-to-use replacement value, not advice."""

    message = _get_client().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )
    
    raw = message.content[0].text.strip()
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    suggestions = []
    for line in lines:
        for prefix in ["1.", "2.", "3.", "4.", "5."]:
            if line.startswith(prefix):
                suggestions.append(line[len(prefix):].strip())
                break
    return suggestions[:5]
