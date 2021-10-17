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

CType = Union[str, dict]


def create_output_dir(output_path: str):
    dirpath, filename = os.path.split(output_path)
    os.makedirs(dirpath, exist_ok=True)


def preprocess_header_file(compiler: str, input_path: str, output_path: str):
    cmd = [compiler, '-E', input_path]
    output: bytes = subprocess.check_output(cmd)
    
    with open(output_path, 'w+b') as f:
        f.write(output)


def _get_func_decl_return_type(n) -> CType:
    return_type: CType

    if isinstance(n.type, c_ast.TypeDecl):
        if isinstance(n.type.type, c_ast.IdentifierType):
            if n.type.type.names:
                return_type = n.type.type.names[0]
            else:
                return_type = 'void'
        else:
            return_type = f'/* _get_func_decl_return_type: Unsupported type {type(n)} */'
    else:
        return_type = f'/* _get_func_decl_return_type: Unsupported type {type(n)} */'

    return return_type


def _get_func_decl_params(n) -> list[tuple[str, CType]]:
    params: list[tuple[str, CType]] = []
    param: tuple[str, CType]

    for m in n.args.params:
        assert isinstance(m, c_ast.Typename)
        if isinstance(m.type, c_ast.PtrDecl):
            if isinstance(m.type.type, c_ast.TypeDecl) and isinstance(m.type.type.type, c_ast.IdentifierType) and m.type.type.type.names[0] == 'char':
                param = (None, 'string')
            else:
                param = (None, 'pointer')
        elif isinstance(m.type, c_ast.TypeDecl) and isinstance(m.type.type, c_ast.IdentifierType):
            type_name: str = m.type.type.names[0]

            if type_name in PRIMITIVE_C_TYPES:
                param = (None, type_name)
            else:
                raise TypeError(f'Unsupported type {type_name!r}')

        params.append(param)

    return params


def _get_func_decl_types(n) -> (str, CType, list[CType]):
    func_name: str
    return_type: CType
    params: list[tuple[str, CType]]

    func_name = n.type.declname
    return_type = _get_func_decl_return_type(n)
    params = _get_func_decl_params(n)

    return func_name, return_type, params


def _get_type_decl(n) -> (str, CType):
    decl_name: str
    decl_type: CType

    # decl_name
    if n.declname:
        decl_name = n.declname
    else:
        decl_name = f'/* _get_type_decl: Unknown decl_name */'

    # decl_type
    if isinstance(n.type, c_ast.IdentifierType):
        if n.type.names:
            decl_type = n.type.names[0]
        else:
            decl_type = f'/* _get_type_decl: Unknown decl_type */'
    else:
        decl_type = f'/* _get_type_decl: Unsupported {n.type} */'

    return decl_name, decl_type


def _get_decl(n) -> (str, CType):
    # name: str
    decl_name: str
    type_decl: CType

    # # name
    # name = n.name

    if isinstance(n.type, c_ast.TypeDecl):
        decl_name, type_decl = _get_type_decl(n.type)
    else:
        decl_name = '/* _get_decl: Unknown name */'
        type_decl = f'/* _get_decl: Unsupported {n.type} */'

    return decl_name, type_decl


def _get_type_decl_types(n) -> (str, CType, list[tuple[str, CType]]):
    type_decl_name: str
    type_decl_type: CType
    decls_fields: list[tuple[str, CType]]

    # type_decl_name
    type_decl_name = n.type.name

    if isinstance(n.type, c_ast.Struct):
        # type_decl_type
        type_decl_type = 'struct'
        
        # decls_fields
        if n.type.decls:
            decls_fields = [_get_decl(m) for m in n.type.decls]
        else:
            decls_fields = []
    else:
        raise TypeError(f'Unsupported type {type(n.type)}')
    
    return type_decl_name, type_decl_type, decls_fields


def get_type_decl(n) -> str:
    type_decl_name, type_decl_type, decls_fields = _get_type_decl_types(n)
    decls_types = [f'/* {field_name} */ {dumps(field_type)}' for field_name, field_type in decls_fields]
    js_line = f'const {type_decl_name} /*: {type_decl_type} */ = {decls_types};'
    return js_line


def get_func_decl(n) -> str:
    func_name, return_type, params = _get_func_decl_types(n)
    params_types = [f'/* {param_name} */ {dumps(param_type)}' for param_name, param_type in params]
    js_line = f'const {func_name} = _quickjs_ffi_wrap_ptr_func_decl(LIB, {func_name!r}, null, {dumps(return_type)}, ...{params_types});'
    return js_line


def get_ptr_func_decl(n) -> str:
    js_line = '/* get_ptr_func_decl */'
    return js_line


def get_typedef(n) -> str:
    js_line: str

    if isinstance(n.type, c_ast.TypeDecl):
        js_line = get_type_decl(n.type)
    elif isinstance(n.type, c_ast.FuncDecl):
        js_line = get_func_decl(n.type)
    elif isinstance(n.type, c_ast.PtrDecl) and isinstance(n.type.type, c_ast.FuncDecl):
        js_line = get_ptr_func_decl(n.type.type)
    else:
        js_line = f'/* get_typedef: Unsupported {type(n.type)} */'
    
    return js_line


def get_decl(n) -> str:
    js_line: str = f'/* get_decl: Unsupported {type(n)} */'
    return js_line


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

    # wrap c code into js
    js_lines: list[str] = [
        "import { CFunction, CCallback } from './quickjs-ffi.js';",
        f"const LIB = {dumps(shared_library)};",
        _QUICKJS_FFI_WRAP_PTR_FUNC_DECL,
    ]

    js_line: str

    for n in file_ast.ext:
        print(n)

        if isinstance(n, c_ast.Typedef):
            js_line = get_typedef(n)
        elif isinstance(n, c_ast.Decl):
            js_line = get_decl(n)
        else:
            js_line = f'/* parse_and_convert: Unsupported type {type(n)} */'

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
