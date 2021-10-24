import os
import argparse
import subprocess
from json import dumps
from typing import Union, Any
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

PRIMITIVE_C_TYPES_NAMES = [
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

PRIMITIVE_C_TYPES = {
    **{n: n for n in PRIMITIVE_C_TYPES_NAMES},
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

USER_DEFINED_DECL = {}
USER_DEFINED_FUNC_DECL = {}
USER_DEFINED_PTR_FUNC_DECL = {}
USER_DEFINED_STRUCT_DECL = {}
USER_DEFINED_ARRAY_DECL = {}
USER_DEFINED_ENUM_DECL = {}
USER_DEFINED_TYPEDEF_STRUCT = {}
USER_DEFINED_TYPEDEF_FUNC_DECL = {}
USER_DEFINED_TYPEDEF_PTR_DECL = {}

USER_DEFINED_TYPES = ChainMap(
    USER_DEFINED_DECL,
    USER_DEFINED_FUNC_DECL,
    USER_DEFINED_PTR_FUNC_DECL,
    USER_DEFINED_STRUCT_DECL,
    USER_DEFINED_ARRAY_DECL,
    USER_DEFINED_ENUM_DECL,
    USER_DEFINED_TYPEDEF_STRUCT,
    USER_DEFINED_TYPEDEF_FUNC_DECL,
    USER_DEFINED_TYPEDEF_PTR_DECL,
)

TYPES = ChainMap(
    PRIMITIVE_C_TYPES,
    USER_DEFINED_TYPES,
)

CType = Union[str, dict]


def get_leaf_node(n):
    if hasattr(n, 'type'):
        return get_leaf_node(n.type)
    else:
        return n


def get_leaf_name(n) -> list[str]:
    if isinstance(n, c_ast.IdentifierType):
        if hasattr(n, 'names'):
            return ' '.join(n.names)
        else:
            return ''
    else:
        return get_leaf_names(n.type)


def get_typedef_type_decl_struct(parent, n) -> (CType, str):
    js_type: CType
    js_line: str
    name: str

    if isinstance(n, c_ast.Struct):
        name = parent.declname or n.name
        
        js_type = {
            'kind': 'TypeDeclStruct',
            'name': name
        }

        USER_DEFINED_TYPEDEF_STRUCT[name] = js_type
        js_line = f'export let {name} /* typedef struct */;'
    else:
        # js_line = f'/* get_typedef_type_decl_struct: {type(n)} */'
        raise TypeError(type(n.type))

    return js_type, js_line


def get_typedef_type_decl(parent, n) -> (CType, str):
    js_type: CType
    js_line: str

    if isinstance(n, c_ast.TypeDecl):
        if isinstance(n.type, c_ast.Struct):
            js_type, js_line = get_typedef_type_decl_struct(n, n.type)
        else:
            # js_line = f'/* get_typedef_type_decl: {type(n)} {type(n.type)} */'
            raise TypeError(type(n.type))
    else:
        # js_line = f'/* get_typedef_type_decl: {type(n)} */'
        raise TypeError(type(n.type))
    
    return js_type, js_line


def get_typedef_func_decl_name(parent, n, typedef=None) -> str:
    name: str = typedef.name
    return name


def get_typedef_func_decl_args_param_list_typename_type_decl(parent, n, typedef=None, func_decl=None) -> CType:
    assert isinstance(n, c_ast.TypeDecl)
    js_type: CType

    if isinstance(n.type, c_ast.IdentifierType):
        name: str = get_leaf_name(n.type)
        js_type = TYPES[name]
    else:
        raise TypeError(type(n.type))

    return js_type


def get_typedef_func_decl_args_param_list_typename_ptr_decl_type_decl(parent, n, typedef=None, func_decl=None) -> CType:
    assert isinstance(n, c_ast.TypeDecl)
    js_type: CType

    if isinstance(n.type, c_ast.IdentifierType):
        js_type = get_leaf_name(n.type)
    else:
        raise TypeError(type(n.type))

    return js_type


def get_typedef_func_decl_args_param_list_typename_ptr_decl(parent, n, typedef=None, func_decl=None) -> CType:
    assert isinstance(n, c_ast.PtrDecl)
    js_type: CType

    if isinstance(n.type, c_ast.TypeDecl):
        t: CType = get_typedef_func_decl_args_param_list_typename_ptr_decl_type_decl(n, n.type, typedef=typedef, func_decl=func_decl)

        if t == 'void':
            js_type = 'pointer'
        else:
            js_type = {
                'kind': 'PtrDecl',
                'type': t,
            }
    else:
        raise TypeError(type(n.type))

    return js_type


def get_typedef_func_decl_args_param_list_typename(parent, n, typedef=None, func_decl=None) -> CType:
    assert isinstance(n, c_ast.Typename)
    js_type: CType

    if isinstance(n.type, c_ast.TypeDecl):
        js_type = get_typedef_func_decl_args_param_list_typename_type_decl(n, n.type, typedef=typedef, func_decl=func_decl)
    elif isinstance(n.type, c_ast.PtrDecl):
        js_type = get_typedef_func_decl_args_param_list_typename_ptr_decl(n, n.type, typedef=typedef, func_decl=func_decl)
    else:
        raise TypeError(type(n.type))

    return js_type


def get_typedef_func_decl_args(parent, n, typedef=None) -> list[CType]:
    js_params_types: list[CType] = []
    assert isinstance(parent, c_ast.FuncDecl)
    args: c_ast.ParamList = parent.args
    assert isinstance(args, c_ast.ParamList)

    for param in args.params:
        assert isinstance(param, c_ast.Typename)
        js_param_type: CType = get_typedef_func_decl_args_param_list_typename(args, param, typedef=typedef, func_decl=parent)
        js_params_types.append(js_param_type)

    return js_params_types


def get_get_typedef_func_decl_type_type_decl(parent, n, typedef=None, func_decl=None) -> CType:
    js_type: CType

    if isinstance(n, c_ast.IdentifierType):
        c_type: str = get_leaf_name(n)
        js_type = TYPES[c_type]
    else:
        raise TypeError(type(n))

    return js_type


def get_typedef_func_decl_type(parent, n, typedef=None) -> CType:
    js_type: CType

    if isinstance(n, c_ast.TypeDecl):
        js_type = get_get_typedef_func_decl_type_type_decl(n, n.type, typedef=typedef, func_decl=parent)
    else:
        raise TypeError(type(n))

    return js_type


def get_typedef_func_decl(parent, n, typedef=None) -> (CType, str):
    print(parent)
    assert parent is None or isinstance(parent, c_ast.Typedef)
    assert isinstance(n, c_ast.FuncDecl)
    js_type: str
    js_line: str
    typedef_name: str

    if isinstance(n, c_ast.FuncDecl):
        if isinstance(n.type, c_ast.TypeDecl):
            if typedef is None:
                typedef = parent

            js_func_name: str = get_typedef_func_decl_name(n, n.type, typedef=typedef)
            js_return_type: CType = get_typedef_func_decl_type(n, n.type, typedef=typedef)
            js_args_types: list[CType] = get_typedef_func_decl_args(n, n.type, typedef=typedef)
            
            js_type = {
                'kind': 'FuncDecl',
                'func_name': js_func_name,
                'return_type': js_return_type,
                'args_types': js_args_types,
            }
            
            USER_DEFINED_TYPEDEF_FUNC_DECL[js_func_name] = js_type
            js_line = f'/* {js_func_name}: {js_return_type} = {js_args_types} */'
        else:
            # js_line = f'/* get_typedef_func_decl {type(n)} {type(n.type)} */'
            raise TypeError(type(n.type))
    else:
        # js_line = f'/* get_typedef_func_decl {type(n)} */'
        raise TypeError(type(n.type))

    return js_type, js_line


def get_typedef_ptr_decl_func_decl(parent, n, typedef=None) -> CType:
    assert isinstance(parent, c_ast.PtrDecl)
    assert isinstance(n, c_ast.FuncDecl)
    assert typedef is None or isinstance(typedef, c_ast.Typedef)
    js_type: CType
    js_line: str
    t: CType
    name: str

    name = typedef.name
    t, _ = get_typedef_func_decl(None, n, typedef=typedef)

    js_type = {
        'kind': 'PtrDecl',
        'name': name,
        'type': t,
    }

    USER_DEFINED_TYPEDEF_PTR_DECL[name] = js_type
    js_line = f'export const {name} = {dumps(js_type)}'
    return js_type, js_line


def get_typedef_ptr_decl(parent, n) -> (CType, str):
    # return '/* get_typedef_ptr_decl_func_decl */'
    assert isinstance(n, c_ast.PtrDecl)
    js_type: CType
    js_line: str

    if isinstance(n.type, c_ast.FuncDecl):
        js_type, js_line = get_typedef_ptr_decl_func_decl(n, n.type, typedef=parent)
    else:
        raise TypeError(type(n.type))

    return js_type, js_line


def get_typedef(parent, n) -> (CType, str):
    js_type: CType
    js_line: str

    if isinstance(n.type, c_ast.TypeDecl):
        js_type, js_line = get_typedef_type_decl(n, n.type)
    elif isinstance(n.type, c_ast.FuncDecl):
        js_type, js_line = get_typedef_func_decl(n, n.type)
    elif isinstance(n.type, c_ast.PtrDecl):
        js_type, js_line = get_typedef_ptr_decl(n, n.type)
    else:
        # js_line = f'/* get_typedef: Unsupported {type(n.type)} */'
        raise TypeError(type(n.type))
    
    return js_type, js_line


def get_enum(parent, n) -> (CType, str):
    js_type: CType
    js_line: str = ''
    values = n.values
    decl_name: str = parent.name
    enum_name: str = n.name
    enum_fields: dict[str, Any] = {}
    last_enum_field_value: int = -1

    assert isinstance(values, c_ast.EnumeratorList)

    for m in values.enumerators:
        enum_field_name: str = m.name
        enum_field_value: Any

        if m.value:
            if isinstance(m.value, c_ast.Constant):
                enum_field_value = eval(m.value.value)
            elif m.value is None:
                enum_field_value = None
            elif isinstance(m.value, c_ast.BinaryOp):
                enum_field_value = eval(f'{m.value.left.value} {m.value.op} {m.value.right.value}')
            elif isinstance(m.value, c_ast.UnaryOp):
                enum_field_value = eval(f'{m.value.op} {m.value.expr.value}')
            else:
                raise TypeError(f'get_enum: Unsupported {type(m.value)}')
        else:
            enum_field_value = last_enum_field_value + 1
        
        last_enum_field_value = enum_field_value
        enum_fields[enum_field_name] = enum_field_value

    if decl_name:
        USER_DEFINED_DECL[decl_name] = enum_fields
        js_line += f'export const {decl_name} = {enum_fields};\n'

    if enum_name:
        USER_DEFINED_ENUM_DECL[enum_name] = enum_fields
        js_line += f'export const {enum_name} = {enum_fields};\n'

    js_type = {
        'kind': 'Enum',
        'name': enum_name or decl_name,
        'type': enum_fields,
    }

    return js_type, js_line


def get_decl_type_decl(parent, n) -> (CType, str):
    js_type: CType
    js_line: str

    if isinstance(n, c_ast.TypeDecl) and isinstance(n.type, c_ast.Enum):
        js_type, js_line = get_enum(parent, n.type)
    else:
        # js_line = f'/* get_decl_type_decl: Unsupported type {n.type} */'
        raise TypeError(type(n.type))

    return js_type, js_line

def get_decl_enum_decl(parent, n) -> (CType, str):
    js_type: CType
    js_line: str

    if isinstance(n, c_ast.Enum):
        js_type, js_line = get_enum(parent, n)
    else:
        # js_line = f'/* get_decl_enum_decl: Unsupported type {n.type} */'
        raise TypeError(type(n.type))

    return js_type, js_line


def get_decl_func_decl(parent, n) -> (CType, str):
    return None, '/* get_decl_func_decl */'


def get_decl_array_decl(parent, n) -> (CType, str):
    return None, '/* get_decl_array_decl */'


def get_decl(parent, n) -> (CType, str):
    js_type: CType
    js_line: str

    if isinstance(n.type, c_ast.TypeDecl):
        js_type, js_line = get_decl_type_decl(n, n.type)
    elif isinstance(n.type, c_ast.Enum):
        js_type, js_line = get_decl_enum_decl(n, n.type)
    elif isinstance(n.type, c_ast.FuncDecl):
        js_type, js_line = get_decl_func_decl(n, n.type)
    elif isinstance(n.type, c_ast.ArrayDecl):
        js_type, js_line = get_decl_array_decl(n, n.type)
    else:
        # js_line = f'/* get_decl: Unsupported {type(n.type)} */'
        raise TypeError(type(n.type))
    
    return js_type, js_line


def get_file_ast(file_ast, shared_library: str) -> str:
    js_lines: list[str]
    js_type: CType
    js_line: str

    js_lines = [
        "import { CFunction, CCallback } from './quickjs-ffi.js';",
        f"const LIB = {dumps(shared_library)};",
        _QUICKJS_FFI_WRAP_PTR_FUNC_DECL,
    ]

    for n in file_ast.ext:
        print(n)

        if isinstance(n, c_ast.Typedef):
            js_type, js_line = get_typedef(file_ast, n)
        elif isinstance(n, c_ast.Decl):
            js_type, js_line = get_decl(file_ast, n)
        else:
            js_type, js_line = f'/* get_file_ast: Unsupported type {type(n)} */'

        js_lines.append(js_line)

    js_lines = '\n'.join(js_lines)
    return js_lines


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

    # wrap C code into JS
    output_data: str = get_file_ast(file_ast, shared_library=shared_library)
    print('-' * 20)
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
