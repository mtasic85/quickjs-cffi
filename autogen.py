import os
import argparse
import subprocess
from json import dumps
from typing import Union

from pycparser import c_ast, parse_file


_QUICKJS_FFI_WRAP_PTR_FUNC_DECL = '''
const _quickjs_ffi_wrap_ptr_func_decl = (lib, name, nargs, ...types) => {
    // wrap C function
    const c_types = types.map(type => {
        if (typeof type == 'string') {
            return type;
        } else if (typeof type == 'object') {
            if (type.type == 'PtrFuncDecl') {
                return 'pointer';
            } else {
                throw new Error('Unsupported type');
            }
        } else {
            throw new Error('Unsupported type');
        }
    });

    const c_func = new CFunction(lib, name, nargs, ...c_types);
    
    const js_func = (...js_args) => {
        const c_args = types.slice(1).map((type, i) => {
            const js_arg = js_args[i];

            if (typeof type == 'string') {
                return js_arg;
            } else if (typeof type == 'object') {
                if (type.type == 'PtrFuncDecl') {
                    const c_cb = new CCallback(js_arg, null, ...type.types);
                    return c_cb.cfuncptr;
                } else {
                    throw new Error('Unsupported type');
                }
            } else {
                throw new Error('Unsupported type');
            }
        });

        return c_func.invoke(...c_args);
    };

    return js_func;
};
'''

PRIMITIVE_C_TYPES = [
    'void',
    'uint8',
    'sint8',
    'uint16',
    'sint16',
    'uint32',
    'sint32',
    'uint64',
    'sint64',
    'float',
    'double',
    'uchar',
    'schar',
    'ushort',
    'sshort',
    'uint',
    'sint',
    'ulong',
    'slong',
    'longdouble',
    'pointer',
    'complex_float',
    'complex_double',
    'complex_longdouble',
    'uint8_t',
    'int8_t',
    'uint16_t',
    'int16_t',
    'uint32_t',
    'int32_t',
    'char',
    'short',
    'int',
    'long',
    'string',
    'uintptr_t',
    'intptr_t',
    'size_t',
]


def create_output_dir(output_path: str):
    dirpath, filename = os.path.split(output_path)
    os.makedirs(dirpath, exist_ok=True)


def preprocess_header_file(compiler: str, input_path: str, output_path: str):
    cmd = [compiler, '-E', input_path]
    output: bytes = subprocess.check_output(cmd)
    
    with open(output_path, 'w+b') as f:
        f.write(output)


def parse_and_convert(compiler: str, shared_library: str, input_path: str, output_path: str):
    # check existance of input_path
    assert os.path.exists(input_path)

    # create destination directory
    create_output_dir(output_path)

    # preprocess input header path
    dirpath, filename = os.path.split(output_path)
    basename, ext = os.path.splitext(filename)
    processed_output_path: str = os.path.join(dirpath, f'{basename}.h')
    preprocess_header_file(compiler, input_path, processed_output_path)

    # parse input header path
    file_ast = parse_file(processed_output_path, use_cpp=True)
    assert isinstance(file_ast, c_ast.FileAST)

    js_lines: list[str] = [
        "import { CFunction, CCallback } from './quickjs-ffi.js';",
        f"const LIBCFLTK = {dumps(shared_library)};",
        _QUICKJS_FFI_WRAP_PTR_FUNC_DECL,
    ]

    def _get_type_decl_types(n) -> (str, str, list[Union[str, dict]]):
        type_decl_type: str
        type_decl_name: str
        types: list[Union[str, dict]]
        t: Union[str, dict]

        type_decl_name = n.type.name

        if isinstance(n.type, c_ast.Struct):
            type_decl_type = 'struct'
            
            if n.type.decls:
                types = [_get_decl(m) for m in n.type.decls]
            else:
                types = []
        else:
            raise TypeError(f'Unsupported type {type(n.type)}')
        
        return type_decl_name, type_decl_type, types

    def _get_func_decl_return_type(n) -> list[Union[str, dict]]:
        # print('!!!', n)
        return 'void'

    def _get_func_decl_params_types(n) -> list[Union[str, dict]]:
        types: list[Union[str, dict]] = []
        t: Union[str, dict]

        for m in n.args.params:
            assert isinstance(m, c_ast.Typename)
            t: list[Union[str, dict]]
            
            if isinstance(m.type, c_ast.PtrDecl):
                if isinstance(m.type.type, c_ast.TypeDecl) and isinstance(m.type.type.type, c_ast.IdentifierType) and m.type.type.type.names[0] == 'char':
                    t = 'string'
                else:
                    t = 'pointer'
            elif isinstance(m.type, c_ast.TypeDecl) and isinstance(m.type.type, c_ast.IdentifierType):
                mt = m.type.type.names[0]

                if mt in PRIMITIVE_C_TYPES:
                    t = mt
                else:
                    raise TypeError(f'Unsupported type {mt!r}')

            types.append(t)

        return types

    def _get_func_decl_types(n) -> list[Union[str, dict]]:
        types: list[Union[str, dict]] = []
        
        # return type
        types.append(_get_func_decl_return_type(n))

        # params types
        types.extend(_get_func_decl_params_types(n))

        return types

    def _get_type_def(n) -> str:
        js_line: str

        if isinstance(n.type, c_ast.TypeDecl):
            type_decl_name: str
            type_decl_type: str
            types: list
            type_decl_name, type_decl_type, types = _get_type_decl_types(n.type)
            js_line = f'const {type_decl_name} /*: {type_decl_type} */ = {dumps(types)};'
        elif isinstance(n.type, c_ast.FuncDecl):
            types: list = _get_func_decl_types(n.type)
            types: str = ', '.join([dumps(t) for t in types])
            js_line = f'const {n.name} = _quickjs_ffi_wrap_ptr_func_decl(LIBCFLTK, {n.name!r}, null, {types});'
        elif isinstance(n.type, c_ast.PtrDecl) and isinstance(n.type.type, c_ast.FuncDecl):
            js_line = f'// Unsupported 1: {type(n.type)}'
        else:
            js_line = f'// Unsupported 2: {type(n.type)}'

        return js_line

    def _get_decl(n) -> str:
        js_line: str = f'// _get_decl: Unsupported {type(n)}'
        return js_line

    for n in file_ast.ext:
        print(type(n), n)
        js_line: str

        if isinstance(n, c_ast.Typedef):
            js_line = _get_type_def(n)
        elif isinstance(n, c_ast.Decl):
            js_line = _get_decl(n)

        js_lines.append(js_line)

    print('-' * 20)
    print('\n'.join(js_lines))


if __name__ == '__main__':
    # cli arg parser
    parser = argparse.ArgumentParser(description='Convert .h to .js')
    parser.add_argument('-c', dest='compiler', default='gcc', help='gcc, clang, tcc')
    parser.add_argument('-l', dest='shared_library', default='./libcfltk.so.1.2.5', help='Shared library')
    parser.add_argument('-i', dest='input_path', help='input .h path')
    parser.add_argument('-o', dest='output_path', help='output .js path')
    
    # parse_and_convert
    args = parser.parse_args()
    parse_and_convert(args.compiler, args.shared_library, args.input_path, args.output_path)
