import os
import argparse
import subprocess
from json import dumps
from typing import Union
from pprint import pprint
from collections import ChainMap

from pycparser import c_ast, parse_file


_QUICKJS_FFI_WRAP_PTR_FUNC_DECL = '''
const __quickjs_ffi_wrap_ptr_func_decl = (lib, name, nargs, ...types) => {
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

    let c_func;

    try {
        c_func = new CFunction(lib, name, nargs, ...c_types);
    } catch (e) {
        c_func = null;
    }
    
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

const _quickjs_ffi_wrap_ptr_func_decl = (lib, name, nargs, ...types) => {
    try {
        return __quickjs_ffi_wrap_ptr_func_decl(lib, name, nargs, ...types);
    } catch (e) {
        return undefined;
    }
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

PRIMITIVE_C_TYPES_ALIASES = {
    **{n: n for n in PRIMITIVE_C_TYPES},
    '_Bool': 'int',
    'signed char': 'schar',
    'unsigned char': 'uchar',
    'signed': 'sint',
    'signed int': 'sint',
    'unsigned': 'uint',
    'unsigned int': 'uint',
    'long long': 'sint64', # FIXME: platform specific
    'signed long': 'uint32', # FIXME: platform specific
    'unsigned long': 'uint32', # FIXME: platform specific
    'signed long long': 'sint64', # FIXME: platform specific
    'unsigned long long': 'uint64', # FIXME: platform specific
    'long double': 'longdouble',
}

USER_DEFINED_TYPE_DECL = {}
USER_DEFINED_TYPEDEF_FUNC_DECL = {}
USER_DEFINED_FUNC_DECL = {}
USER_DEFINED_PTR_FUNC_DECL = {}
USER_DEFINED_ARRAY_DECL = {}
USER_DEFINED_ENUM_DECL = {}

USER_DEFINED_TYPES = ChainMap(
    USER_DEFINED_TYPE_DECL,
    USER_DEFINED_TYPEDEF_FUNC_DECL,
    USER_DEFINED_FUNC_DECL,
    USER_DEFINED_PTR_FUNC_DECL,
    USER_DEFINED_ARRAY_DECL,
    USER_DEFINED_ENUM_DECL,
)

TYPES = ChainMap(
    PRIMITIVE_C_TYPES_ALIASES,
    USER_DEFINED_TYPES,
)

CType = Union[str, dict]


def create_output_dir(output_path: str):
    dirpath, filename = os.path.split(output_path)
    os.makedirs(dirpath, exist_ok=True)


def preprocess_header_file(compiler: str, input_path: str, output_path: str):
    cmd = [compiler, '-E', input_path]
    output: bytes = subprocess.check_output(cmd)
    
    with open(output_path, 'w+b') as f:
        f.write(output)


def _get_compatible_type_name(name: Union[str, list[str]]) -> str:
    if isinstance(name, list):
        name = ' '.join(name)

    return PRIMITIVE_C_TYPES_ALIASES.get(name, name)


def _get_leaf_names(n) -> list[str]:
    if isinstance(n, c_ast.IdentifierType):
        if hasattr(n, 'names'):
            return n.names
        else:
            return []
    else:
        return _get_leaf_names(n.type)


def _get_func_decl_func_name(n, kind: str) -> str:
    func_name: str

    if isinstance(n.type, c_ast.TypeDecl):
        func_name = n.type.declname
    elif isinstance(n.type, c_ast.PtrDecl):
        if isinstance(n.type.type, c_ast.TypeDecl):
            func_name = n.type.type.declname
        elif isinstance(n.type.type, c_ast.PtrDecl):
            if isinstance(n.type.type.type, c_ast.TypeDecl):
                func_name = n.type.type.type.declname
            else:
                raise TypeError(f'_get_func_decl_types: Unsupported type {n.type.type.type}')
        else:
            raise TypeError(f'_get_func_decl_types: Unsupported type {n.type.type}')
    else:
        raise TypeError(f'_get_func_decl_types: Unsupported type {n.type}')

    return func_name


def _get_func_decl_return_type(n, kind: str) -> CType:
    return_type: CType

    if isinstance(n, c_ast.FuncDecl):
        if isinstance(n.type, c_ast.PtrDecl):
            return_type = 'pointer'
        elif isinstance(n.type, c_ast.TypeDecl):
            if isinstance(n.type.type, c_ast.IdentifierType):
                return_type: str = _get_compatible_type_name(n.type.type.names)

                if return_type in PRIMITIVE_C_TYPES:
                    pass
                elif return_type in TYPES:
                    return_type = TYPES[return_type]
                else:
                    raise TypeError(f'Unsupported type {return_type!r}')
            else:
                return_type = f'/* _get_func_decl_return_type: -3 Unsupported type {type(n)}, {type(n.type)}, {type(n.type.type)} */'
        else:
            return_type = f'/* _get_func_decl_return_type: -2 Unsupported type {type(n)}, {type(n.type)} */'
    else:
        if isinstance(n.type, c_ast.TypeDecl):
            if isinstance(n.type.type, c_ast.IdentifierType):
                if n.type.type.names:
                    return_type = _get_compatible_type_name(n.type.type.names)
                else:
                    return_type = 'void'
            else:
                return_type = f'/* _get_func_decl_return_type: 1 Unsupported type {type(n)} */'
        elif isinstance(n.type, c_ast.FuncDecl):
            if isinstance(n.type.type, c_ast.PtrDecl):
                return_type = 'pointer'
            else:
                return_type = f'/* _get_func_decl_return_type: 2 Unsupported type {type(n)} */'
        else:
            return_type = f'/* _get_func_decl_return_type: 3 Unsupported type {type(n)} */'

    return return_type


'''
def _get_func_decl_params(n, kind: str) -> list[tuple[str, CType]]:
    params: list[tuple[str, CType]] = []
    param: tuple[str, CType]

    for m in n.args.params:
        assert isinstance(m, (c_ast.Typename, c_ast.Decl))
        name = m.name
        type_name: str = _get_compatible_type_name(_get_leaf_names(n))

        if isinstance(m.type, c_ast.PtrDecl):
            # if type_name == 'Fl_Callback':
            #     raise TypeError((kind, name, type_name))

            if kind == 'TypedefFuncDecl' or kind == 'FuncDecl':
                if isinstance(m.type.type, c_ast.TypeDecl) and isinstance(m.type.type.type, c_ast.IdentifierType):
                    if type_name == 'char':
                        param = (name, 'string')
                    elif type_name in TYPES:
                        t = TYPES[type_name]

                        if isinstance(t, dict) and t['type'] == 'FuncDecl':
                            t = {'type': 'PtrFuncDecl', 'types': t['types']}

                        if type_name == 'Fl_Callback':
                            # pprint(TYPES)
                            # raise TypeError((kind, name, type_name, t))
                            print('!', kind, name, type_name, t)
                            input()

                        param = (name, t)
                    else:
                        # param = (name, 'pointer')
                        raise TypeError(f'Unsupported type {type_name!r}')
                else:
                    param = (name, 'pointer')
            elif kind == 'PtrFuncDecl':
                # if type_name in TYPES:
                #     param = (name, TYPES[type_name])
                # else:
                #     param = (name, 'pointer')
                param = (name, 'pointer')
            else:
                param = (name, 'pointer')
        elif isinstance(m.type, c_ast.TypeDecl) and isinstance(m.type.type, c_ast.IdentifierType):
            if type_name in PRIMITIVE_C_TYPES:
                param = (name, type_name)
            elif type_name in TYPES:
                param = (None, TYPES[type_name])
            else:
                raise TypeError(f'Unsupported type {type_name!r}')

        params.append(param)

    return params
'''
'''
def _get_func_decl_params(n, kind: str) -> list[tuple[str, CType]]:
    params: list[tuple[str, CType]] = []
    param: tuple[str, CType]

    for m in n.args.params:
        assert isinstance(m, (c_ast.Typename, c_ast.Decl))
        name = m.name
        type_name: str = _get_compatible_type_name(_get_leaf_names(n))
        
        # if type_name == 'Fl_Callback':
        #     raise TypeError((kind, name, type_name))

        if isinstance(m.type, c_ast.PtrDecl):
            if kind == 'TypedefFuncDecl' or kind == 'FuncDecl':
                if isinstance(m.type.type, c_ast.TypeDecl) and isinstance(m.type.type.type, c_ast.IdentifierType):
                    if type_name == 'char':
                        param = (name, 'string')
                    elif type_name in TYPES:
                        t = TYPES[type_name]

                        if isinstance(t, dict) and t['type'] in ('TypedefFuncDecl', 'FuncDecl'):
                            t = {'type': 'PtrFuncDecl', 'types': t['types']}

                        if type_name == 'Fl_Callback':
                            # pprint(TYPES)
                            # raise TypeError((kind, name, type_name, t))
                            print('!', kind, name, type_name, type(t), t)
                            input()

                        param = (name, t)
                    else:
                        # param = (name, 'pointer')
                        raise TypeError(f'Unsupported type {type_name!r}')
                else:
                    param = (name, 'pointer')
            elif kind == 'PtrFuncDecl':
                # if type_name in TYPES:
                #     param = (name, TYPES[type_name])
                # else:
                #     param = (name, 'pointer')
                # print('!', kind, name, type_name)
                param = (name, 'pointer')
            else:
                param = (name, 'pointer')
        elif isinstance(m.type, c_ast.TypeDecl) and isinstance(m.type.type, c_ast.IdentifierType):
            if type_name in TYPES:
                param = (name, TYPES[type_name])
            else:
                raise TypeError(f'Unsupported type {type_name!r}')

        params.append(param)

    return params
'''

def _get_func_decl_params(n, kind: str) -> list[tuple[str, CType]]:
    params: list[tuple[str, CType]] = []
    param: tuple[str, CType]

    for m in n.args.params:
        assert isinstance(m, (c_ast.Typename, c_ast.Decl))
        name = m.name
        type_name: str = _get_compatible_type_name(_get_leaf_names(n))
        
        # if type_name == 'Fl_Callback':
        #     raise TypeError((kind, name, type_name))

        if isinstance(m.type, c_ast.PtrDecl):
            if kind == 'TypedefFuncDecl' or kind == 'FuncDecl':
                if isinstance(m.type.type, c_ast.TypeDecl) and isinstance(m.type.type.type, c_ast.IdentifierType):
                    if type_name == 'char':
                        param = (name, 'string')
                    elif type_name in TYPES:
                        t = TYPES[type_name]

                        if isinstance(t, dict) and t['type'] in ('TypedefFuncDecl', 'FuncDecl'):
                            t = {'type': 'PtrFuncDecl', 'types': t['types']}

                        if type_name == 'Fl_Callback':
                            # pprint(TYPES)
                            # raise TypeError((kind, name, type_name, t))
                            print('!', kind, name, type_name, type(t), t)
                            input()

                        param = (name, t)
                    else:
                        # param = (name, 'pointer')
                        raise TypeError(f'Unsupported type {type_name!r}')
                else:
                    param = (name, 'pointer')
            elif kind == 'PtrFuncDecl':
                # if type_name in TYPES:
                #     param = (name, TYPES[type_name])
                # else:
                #     param = (name, 'pointer')
                # print('!', kind, name, type_name)
                param = (name, 'pointer')
            else:
                param = (name, 'pointer')
        elif isinstance(m.type, c_ast.TypeDecl) and isinstance(m.type.type, c_ast.IdentifierType):
            if type_name in TYPES:
                param = (name, TYPES[type_name])
            else:
                raise TypeError(f'Unsupported type {type_name!r}')

        params.append(param)

    return params


def _get_func_decl_types(n, kind: str) -> (str, CType, list[CType]):
    func_name: str = _get_func_decl_func_name(n, kind)
    return_type: CType = _get_func_decl_return_type(n, kind)
    params: list[tuple[str, CType]] = _get_func_decl_params(n, kind)
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
            decl_type = _get_compatible_type_name(n.type.names)
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
    elif isinstance(n.type, c_ast.Union):
        # type_decl_type
        type_decl_type = 'union'

        # decls_fields
        if n.type.decls:
            decls_fields = [_get_decl(m) for m in n.type.decls]
        else:
            decls_fields = []
    else:
        raise TypeError(f'Unsupported type {type(n.type)}')
    
    return type_decl_name, type_decl_type, decls_fields


def get_type_decl(n, name=None) -> str:
    # type_decl_type is always "struct"
    type_decl_name, type_decl_type, type_decls_fields = _get_type_decl_types(n)
    type_decl_name = type_decl_name or name

    type_decls_types = [f'/* {field_name} */ {dumps(field_type)}' for field_name, field_type in type_decls_fields]
    js_line = f'export const {type_decl_name} /*: {type_decl_type} */ = {type_decls_types};'
    
    if type_decl_name in TYPES:
        js_line = f'// {js_line}'
    
    USER_DEFINED_TYPE_DECL[type_decl_name] = type_decls_fields
    return js_line


def get_typedef_func_decl(n) -> str:
    func_name, return_type, params = _get_func_decl_types(n, kind='TypedefFuncDecl')
    params_types = [f'/* {param_name} */ {dumps(param_type)}' for param_name, param_type in params]
    js_line = f'export const {func_name} = {{"type": "FuncDecl", "name": {dumps(func_name)}, "types": [{dumps(return_type)}, [{", ".join(params_types)}]]}};'

    if func_name in TYPES:
        js_line = f'// {js_line}'

    USER_DEFINED_TYPEDEF_FUNC_DECL[func_name] = {
        'type': 'TypedefFuncDecl',
        'name': func_name,
        'types': [return_type, *[param_type for param_name, param_type in params]],
    }

    # FIXME: remove this code block
    # if func_name == 'Fl_Callback':
    #     print(USER_DEFINED_TYPEDEF_FUNC_DECL)
    #     raise TypeError(func_name)

    return js_line


def get_func_decl(n) -> str:
    func_name, return_type, params = _get_func_decl_types(n, kind='FuncDecl')
    params_types = [f'/* {param_name} */ {dumps(param_type)}' for param_name, param_type in params]
    js_line = f'export const {func_name} = _quickjs_ffi_wrap_ptr_func_decl(LIB, {func_name!r}, null, {dumps(return_type)}, ...[{", ".join(params_types)}]);'

    if func_name in TYPES:
        js_line = f'// {js_line}'

    USER_DEFINED_FUNC_DECL[func_name] = {
        'type': 'FuncDecl',
        'name': func_name,
        'types': [return_type, *[param_type for param_name, param_type in params]],
    }

    return js_line


def get_ptr_func_decl(n) -> str:
    func_name, return_type, params = _get_func_decl_types(n.type, kind='PtrFuncDecl')
    params_types = [f'/* {param_name} */ {dumps(param_type)}' for param_name, param_type in params]
    js_line = f'export const {func_name} /* : "function pointer" */ = [{dumps(return_type)}, ...[{", ".join(params_types)}]];'

    if func_name in TYPES:
        js_line = f'// {js_line}'

    USER_DEFINED_PTR_FUNC_DECL[func_name] = 'pointer'
    return js_line


def get_array_decl(n) -> str:
    js_line: str
    array_var_name: str
    items: list = []

    if isinstance(n.type, c_ast.ArrayDecl):
        array_var_name = n.name

        if isinstance(n.type.type, c_ast.PtrDecl):
            if isinstance(n.type.type.type, c_ast.TypeDecl):
                if isinstance(n.type.type.type.type, c_ast.IdentifierType):
                    type_name: str = _get_compatible_type_name(n.type.type.type.type.names)

                    if type_name == 'char':
                        type_name = 'string'

                    if isinstance(n.init, c_ast.InitList):
                        for m in n.init.exprs:
                            if isinstance(m, c_ast.Constant):
                                if m.type == 'string':
                                    item = m.value[1:-1]
                                else:
                                    item = m.value

                                items.append(item)

                    else:
                        raise TypeError(f'get_array_decl: Unsupported {type(n.init)}')
                else:
                    raise TypeError(f'get_array_decl: Unsupported {type(n.type.type.type.type)}')
            else:
                raise TypeError(f'get_array_decl: Unsupported {type(n.type.type.type)}')
        else:
            raise TypeError(f'get_array_decl: Unsupported {type(n.type.type)}')
    else:
        raise TypeError(f'get_array_decl: Unsupported {type(n.type)}')

    js_line = f'const {array_var_name} = {dumps(items)};'

    if array_var_name in TYPES:
        js_line = f'// {js_line}'

    USER_DEFINED_ARRAY_DECL[array_var_name] = items
    return js_line


def get_enum_decl(n) -> str:
    js_line: str
    enum_var_name: str
    items: dict = {}

    if isinstance(n, c_ast.Enum):
        enum_var_name = n.name

        if isinstance(n.values, c_ast.EnumeratorList):
            for m in n.values.enumerators:
                if isinstance(m, c_ast.Enumerator):
                    k = m.name

                    if isinstance(m.value, c_ast.Constant):
                        v = eval(m.value.value)
                    elif m.value is None:
                        v = None
                    elif isinstance(m.value, c_ast.BinaryOp):
                        v = eval(f'{m.value.left.value} {m.value.op} {m.value.right.value}')
                    elif isinstance(m.value, c_ast.UnaryOp):
                        v = eval(f'{m.value.op} {m.value.expr.value}')
                    else:
                        raise TypeError(f'get_enum_decl: Unsupported {type(m.value)}')

                    items[k] = v
                else:
                    raise TypeError(f'get_enum_decl: Unsupported {type(m)}')
        else:
            raise TypeError(f'get_enum_decl: Unsupported {type(n.values)}')
    else:
        raise TypeError(f'get_enum_decl: Unsupported {type(n)}')

    js_line = f'const {enum_var_name} = {dumps(items)};'

    if enum_var_name in TYPES:
        js_line = f'// {js_line}'

    USER_DEFINED_ENUM_DECL[enum_var_name] = items
    return js_line


def get_typedef(n) -> str:
    js_line: str

    if isinstance(n.type, c_ast.TypeDecl):
        js_line = get_type_decl(n.type, name=n.name)
    elif isinstance(n.type, c_ast.FuncDecl):
        js_line = get_typedef_func_decl(n.type)
    elif isinstance(n.type, c_ast.PtrDecl) and isinstance(n.type.type, c_ast.FuncDecl):
        js_line = get_ptr_func_decl(n.type)
    else:
        js_line = f'/* get_typedef: Unsupported {type(n.type)} */'
    
    return js_line


def get_decl(n) -> str:
    js_line: str

    if isinstance(n.type, c_ast.FuncDecl):
        js_line = get_func_decl(n.type)
    elif isinstance(n.type, c_ast.ArrayDecl):
        js_line = get_array_decl(n)
    elif isinstance(n.type, c_ast.Enum):
        js_line = get_enum_decl(n.type)
    else:
        js_line = f'/* get_decl: Unsupported {type(n.type)} */'
    
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

    output_data: str = '\n'.join(js_lines)
    print(output_data)

    with open(output_path, 'w+') as f:
        f.write(output_data)

    pprint(TYPES)


if __name__ == '__main__':
    # cli arg parser
    parser = argparse.ArgumentParser(description='Convert .h to .js')
    parser.add_argument('-c', dest='compiler', default='gcc', help='gcc, clang, tcc')
    parser.add_argument('-l', dest='shared_library', default='./libcfltk.so', help='Shared library')
    parser.add_argument('-i', dest='input_path', help='input .h path')
    parser.add_argument('-o', dest='output_path', help='output .js path')
    
    # parse_and_convert
    args = parser.parse_args()
    parse_and_convert(args.compiler, args.shared_library, args.input_path, args.output_path)
