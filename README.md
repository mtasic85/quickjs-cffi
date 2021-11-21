# quickjs-cffi

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

### FLTK 1.3
```bash
# multiple files
python autogen.py -sizeof-cflags="-I../cfltk/include" -sizeof-include="cfl_box.h,cfl_browser.h,cfl_button.h,cfl_dialog.h,cfl_draw.h,cfl_enums.h,cfl_group.h,cfl.h,cfl_image.h,cfl_input.h,cfl_macros.h,cfl_menu.h,cfl_misc.h,cfl_printer.h,cfl_surface.h,cfl_table.h,cfl_text.h,cfl_tree.h,cfl_utils.h,cfl_valuator.h,cfl_widget.h,cfl_window.h" -i ../cfltk/include -o ../quickjs-fltk

# single bundle file
python autogen.py -sizeof-cflags="-I../cfltk/include" -sizeof-include="cfl_box.h,cfl_browser.h,cfl_button.h,cfl_dialog.h,cfl_draw.h,cfl_enums.h,cfl_group.h,cfl.h,cfl_image.h,cfl_input.h,cfl_macros.h,cfl_menu.h,cfl_misc.h,cfl_printer.h,cfl_surface.h,cfl_table.h,cfl_text.h,cfl_tree.h,cfl_utils.h,cfl_valuator.h,cfl_widget.h,cfl_window.h" -i ../cfltk/include -o ../quickjs-fltk/fltk.js
```

### libuv
```bash
python autogen.py -fc-cflags="-I../libuv/include -D__GNUC__=3 -DDIR=void" -sizeof-cflags="-I../libuv/include" -sizeof-include="uv.h" -i ../libuv/include/uv.h -o ../quickjs-libuv/uv.js -l libuv.so
```

### gtk-3.0
```bash
python autogen.py -fc-cflags "`pkg-config --cflags gtk+-3.0`" -i /usr/include/gtk-3.0/gtk/gtk.h -o ../quickjs-gtk-3.0
```

### SDL2
```bash
python autogen.py -fc-cflags "`pkg-config --cflags sdl2`" -i /usr/include/SDL2 -o ../quickjs-SDL2
```
