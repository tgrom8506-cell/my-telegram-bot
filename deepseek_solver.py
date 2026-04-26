import requests
import json
import logging
import re
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL

logger = logging.getLogger(__name__)

def clean_short_answer(text: str) -> str:
    """Очистка для короткого режима (только число)"""
    text = re.sub(r'\\[\(\[\]].*?\\[\)\]]', '', text, flags=re.DOTALL)
    text = re.sub(r'[*_`~\\|#]', '', text)
    text = re.sub(r'\b(Ответ|Шаг|Решение|Итог|Проверка|Пример|Складываем|Умножаем|Делим|Разложим)\b\.?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        return text
    parts = text.split()
    if len(parts) > 3:
        numbers = re.findall(r'\d+', text)
        if numbers:
            return numbers[0]
        return ' '.join(parts[:2])
    return text

def clean_full_answer(text: str) -> str:
    """Убирает ** и LaTeX-скобки из подробного ответа, оставляя пояснения"""
    # Убираем **жирный текст**
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    # Убираем LaTeX-обрамление \[ ... \] и \( ... \)
    text = re.sub(r'\\[\(\[\]]', '', text)
    text = re.sub(r'\\[\)\]]', '', text)
    # Убираем возможные двойные пробелы
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def format_steps_with_spacing(text: str) -> str:
    """Вставляет пустую строку перед каждым шагом вида '1. ...', '2. ...'"""
    lines = text.split('\n')
    result = []
    prev_was_step = False
    for line in lines:
        if re.match(r'^\s*\d+\.', line):
            if result and not prev_was_step:
                result.append('')  # пустая строка перед шагом
            result.append(line)
            prev_was_step = True
        else:
            result.append(line)
            prev_was_step = False
    return '\n'.join(result)

def solve_with_deepseek(problem_text: str, image_url: str = None, mode: int = 1) -> str:
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    if mode == 0:   # короткий режим
        system_content = (
            "Ты — помощник по математике. Отвечай только числом или очень короткой фразой (максимум 5 слов). "
            "ЗАПРЕЩЕНО использовать LaTeX-разметку, символы *, _, ^, { }, [ ], ( ), \\, слеши, кавычки, формулы. "
            "Не пиши 'Шаг', 'Ответ', 'Решение', 'Пример', 'Складываем', 'Умножаем'. Просто число или результат. Например: 434. "
            "Никаких объяснений."
        )
        max_tokens = 100
    else:           # подробный режим
        system_content = (
            "Ты — опытный репетитор по математике и программированию. "
            "Решай задачи пошагово. Каждый шаг начинай с новой строки, используй цифры (1., 2., ...). "
            "Между шагами не нужно вставлять пустые строки вручную, они будут добавлены автоматически позже. "
            "НЕ используй LaTeX-разметку (не ставь символы \\[ \\] \\( \\) и звёздочки **). "
            "Используй обычный текст, цифры и знаки + - * / =. "
            "В конце ответа ставь ключевые слова: #математика или #программирование."
        )
        max_tokens = 2000
    
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": problem_text + (f" (изображение: {image_url})" if image_url else "")}
    ]
    
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens
    }
    
    try:
        response = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        answer = response.json()["choices"][0]["message"]["content"].strip()
        
        if mode == 0:
            answer = clean_short_answer(answer)
        else:
            answer = clean_full_answer(answer)
            answer = format_steps_with_spacing(answer)
        return answer
    except Exception as e:
        logger.error(f"Ошибка DeepSeek: {e}")
        return "❌ Ошибка при решении задачи"
