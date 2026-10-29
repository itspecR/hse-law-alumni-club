import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "text_style", Path(__file__).resolve().parents[1] / "checks/check-text-style.py"
)
text_style = importlib.util.module_from_spec(spec)
spec.loader.exec_module(text_style)


class TextStyleTests(unittest.TestCase):
    def test_python_texts_with_yo_or_dashes_are_detected(self):
        for code in ['message = "Заявка сохранён"', 'title = f"{name} – клуб"', 'label = "Клуб — выпускники"']:
            self.assertTrue(text_style.python_failures(code))

    def test_python_logic_logs_and_legal_texts_are_skipped(self):
        for code in [
            'value = text.replace("ё", "е")',
            'match = re.search("[а-яё]", text)',
            'logger.warning("Не удалось записать счётчик")',
            'def slugify(value):\n    return "абвгдеё"',
            'def support_config(settings):\n    return "Данные ещё не определены"',
        ]:
            self.assertEqual(text_style.python_failures(code), [])

    def test_javascript_strings_are_checked_and_regex_skipped(self):
        self.assertTrue(text_style.javascript_failures('const text = "Заявка – отправлена";'))
        self.assertTrue(text_style.javascript_failures("const text = `Сохранён ${count}`;"))
        self.assertEqual(text_style.javascript_failures('const value = text.replace(/ё/g, "е");'), [])

    def test_data_texts_are_checked_and_search_triggers_skipped(self):
        self.assertTrue(text_style.data_failures({"answers": [{"text": "Ещё ответ"}]}))
        self.assertEqual(text_style.data_failures({"answers": [{"triggers": ["счёт"], "text": "Ответ"}]}), [])

    def test_templates_are_checked_by_line(self):
        self.assertEqual(text_style.template_failures("<p>Клуб</p>\n<p>Её</p>"), [(2, "<p>Её</p>")])

    def test_training_office_term_is_detected(self):
        self.assertTrue(text_style.python_failures('text = "Учебный офис свяжется с Вами."'))
        self.assertTrue(text_style.template_failures("<p>Проверку ведет учебный&nbsp;офис.</p>"))
        self.assertTrue(text_style.data_failures({"text": "после подтверждения учебным офисом"}))
        self.assertEqual(text_style.template_failures("<p>Панель управления клуба</p>"), [])

    def test_old_club_name_is_detected(self):
        self.assertTrue(text_style.python_failures('name = "Клуб выпускников факультета права Вышки"'))
        self.assertTrue(text_style.template_failures("<small>факультета права&nbsp;Вышки</small>"))
        self.assertEqual(text_style.template_failures("<p>Клуб выпускников факультета права НИУ ВШЭ</p>"), [])


if __name__ == "__main__":
    unittest.main()
