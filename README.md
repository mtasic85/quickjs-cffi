# cparser-quickjs-ffi

## Setup "C Headers" to "JavaScript" Translator

```bash
python -m venv venv
source venv/bin/activate
cd ../pycparser
python setup.py build
python setup.py install
pip install -r requirements.txt
```

## Run Translator
```bash
source venv/bin/activate
python autogen.py -i ../cfltk/include -o ../quickjs-fltk
python autogen.py -i ../cfltk/include -o ../quickjs-fltk/fltk.js
```

## Examples

After `quickjs-fltk` JavaScript files are generated, you can try some exampes below:
```bash
./qjs hello.js
./qjs hello_single_import.js
```

## Misc

### gtk-3.0
```bash
python autogen.py -fc-cflags "`pkg-config --cflags gtk+-3.0`" -i /usr/include/gtk-3.0/gtk/gtk.h -o ../quickjs-gtk-3.0
```

### SDL2
```bash
python autogen.py -fc-cflags "`pkg-config --cflags sdl2`" -i /usr/include/SDL2 -o ../quickjs-SDL2
```
