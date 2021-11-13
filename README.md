# Setup Local Env

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python autogen.py -i ../cfltk/include -o ../quickjs-fltk
python autogen.py -i ../cfltk/include -o ../quickjs-fltk/fltk.js
```
