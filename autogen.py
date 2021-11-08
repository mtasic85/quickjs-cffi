import os
import argparse
import traceback
import subprocess
from json import dumps
from copy import deepcopy
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
            if (type.kind == 'PtrFuncDecl') {
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
        c_func = undefined;
    }
    
    const js_func = (...js_args) => {
        const c_args = types.slice(1).map((type, i) => {
            const js_arg = js_args[i];

            if (typeof type == 'string') {
                return js_arg;
            } else if (typeof type == 'object') {
                if (type.kind == 'PtrFuncDecl') {
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


CType = Union[str, dict]
JsTypeLine = (CType, str)


class CParser:
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


    def __init__(self):
        self.USER_DEFINED_TYPE_DECL = {}
        self.USER_DEFINED_FUNC_DECL = {}
        self.USER_DEFINED_STRUCT_DECL = {}
        self.USER_DEFINED_UNION_DECL = {}
        self.USER_DEFINED_ENUM_DECL = {}
        self.USER_DEFINED_ARRAY_DECL = {}

        self.USER_DEFINED_TYPEDEF_STRUCT = {}
        self.USER_DEFINED_TYPEDEF_UNION = {}
        self.USER_DEFINED_TYPEDEF_ENUM = {}
        self.USER_DEFINED_TYPEDEF_FUNC_DECL = {}
        self.USER_DEFINED_TYPEDEF_PTR_DECL = {}

        self.USER_DEFINED_DECL = ChainMap(
            self.USER_DEFINED_TYPE_DECL,
            self.USER_DEFINED_FUNC_DECL,
            self.USER_DEFINED_STRUCT_DECL,
            self.USER_DEFINED_UNION_DECL,
            self.USER_DEFINED_ENUM_DECL,
            self.USER_DEFINED_ARRAY_DECL,
        )

        self.USER_DEFINED_TYPEDEF = ChainMap(
            self.USER_DEFINED_TYPEDEF_STRUCT,
            self.USER_DEFINED_TYPEDEF_UNION,
            self.USER_DEFINED_TYPEDEF_ENUM,
            self.USER_DEFINED_TYPEDEF_FUNC_DECL,
            self.USER_DEFINED_TYPEDEF_PTR_DECL,
        )

        self.USER_DEFINED_TYPES = ChainMap(
            self.USER_DEFINED_DECL,
            self.USER_DEFINED_TYPEDEF,
        )

        self.TYPES = ChainMap(
            self.PRIMITIVE_C_TYPES,
            self.USER_DEFINED_TYPES,
        )


    def get_leaf_node(self, n):
        if hasattr(n, 'type'):
            return self.get_leaf_node(n.type)
        else:
            return n


    def get_leaf_name(self, n) -> list[str]:
        if isinstance(n, c_ast.IdentifierType):
            if hasattr(n, 'names'):
                return ' '.join(n.names)
            else:
                return ''
        else:
            return self.get_leaf_names(n.type)


    def get_typename(self, n, decl=None, func_decl=None) -> JsTypeLine:
        js_type: CType = None
        js_line: str = '/* unset */'
        js_name: str | None = None

        if decl:
            raise TypeError(type(n))
        elif func_decl:
            js_name = n.name
            t, _ = self.get_node(n.type, func_decl=func_decl)

            js_type = {
                'kind': 'Typename',
                'name': js_name,
                'type': t,
            }

            js_line = f'typename (func_decl): {dumps(js_type)}'
        else:
            raise TypeError(type(n))

        return js_type, js_line


    def get_type_decl(self, n, typedef=None, decl=None, func_decl=None) -> JsTypeLine:
        js_type: CType = None
        js_line: str = '/* unset */'
        js_name: str | None = None

        if typedef:
            js_name = typedef.name

            if isinstance(n.type, c_ast.Struct):
                t, _ = self.get_struct(n.type, typedef=typedef, type_decl=n)
                
                js_type = {
                    'kind': 'TypeDecl',
                    'name': js_name,
                    'type': t,
                }

                if js_name:
                    self.USER_DEFINED_TYPEDEF_STRUCT[js_name] = js_type

                js_line = f'type_decl (typedef) struct: {dumps(js_type)}'
            else:
                raise TypeError(n)
        elif decl or func_decl:
            if isinstance(n.type, c_ast.Enum):
                t, _ = self.get_enum(n.type, type_decl=n)
                js_name = n.declname

                js_type = {
                    'kind': 'TypeDecl',
                    'name': js_name,
                    'type': t,
                }

                if js_name:
                    self.USER_DEFINED_ENUM_DECL[js_name] = js_type

                js_line = f'type_decl (decl/func_decl) enum: {dumps(js_type)}'
            elif isinstance(n.type, c_ast.PtrDecl):
                t, _ = self.get_ptr_decl(n.type, decl=decl, func_decl=func_decl)
                js_name = decl.name

                js_type = {
                    'kind': 'TypeDecl',
                    'name': js_name,
                    'type': t,
                }

                js_line = f'type_decl (decl/func_decl) ptr_decl: {dumps(js_type)}'
            elif isinstance(n.type, c_ast.IdentifierType):
                js_type = self.get_leaf_name(n.type) # str repo of type in C
                js_line = f'type_decl identifier: {js_type}'
            else:
                raise TypeError(n)
        else:
            raise TypeError(n)

        if js_name:
            self.USER_DEFINED_TYPE_DECL[js_name] = js_type

        return js_type, js_line


    def get_ptr_decl(self, n, typedef=None, decl=None, func_decl=None) -> JsTypeLine:
        js_type: CType = None
        js_line: str = '/* unset */'
        js_name: str | None = None

        if typedef:
            t, _ = self.get_node(n.type, typedef=typedef, ptr_decl=n)
            js_name = typedef.name

            js_type = {
                'kind': 'PtrDecl',
                'name': js_name,
                'type': t,
            }

            if js_name:
                self.USER_DEFINED_TYPEDEF_PTR_DECL[js_name] = js_type
        elif decl:
            t, _ = self.get_node(n.type, decl=decl, ptr_decl=n)
            js_name = None # NOTE: in this implementation is always None, but can be set to real name

            js_type = {
                'kind': 'PtrDecl',
                'name': js_name,
                'type': t,
            }
        elif func_decl:
            t, _ = self.get_node(n.type, func_decl=func_decl, ptr_decl=n)
            js_name = None # NOTE: in this implementation is always None, but can be set to real name

            js_type = {
                'kind': 'PtrDecl',
                'name': js_name,
                'type': t,
            }
        else:
            raise TypeError(type(n))
        
        return js_type, js_line


    def get_struct(self, n, typedef=None, type_decl=None) -> JsTypeLine:
        js_type: CType = None
        js_line: str = '/* unset */'
        js_name: str
        js_fields: dict
        
        if n.name:
            js_name = n.name
        elif type_decl and type_decl.declname:
            js_name = type_decl.declname
        elif typedef and typedef.name:
            js_name = typedef.name
        else:
            raise ValueError(f'Could not get name of struct node {n}')

        # NOTE: does not parse struct fields
        js_fields = {}

        js_type = {
            'kind': 'Struct',
            'name': js_name,
            'fields': js_fields,
        }

        if js_name:
            self.USER_DEFINED_STRUCT_DECL[js_name] = js_type

        js_line = f'struct: {dumps(js_type)}'
        return js_type, js_line


    def get_enum(self, n, decl=None, type_decl=None) -> JsTypeLine:
        js_type: CType
        js_line: str

        if decl or type_decl:
            assert isinstance(n.values, c_ast.EnumeratorList)
            assert isinstance(n.values.enumerators, list)
            last_enum_field_value: int = -1

            js_type = {
                'kind': 'Enum',
                'name': n.name,
                'items': {},
            }

            for m in n.values.enumerators:
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
                js_type['items'][enum_field_name] = enum_field_value

            self.USER_DEFINED_ENUM_DECL[js_type["name"]] = js_type
            js_line = f'enum: {dumps(js_type)}'
        else:
            raise TypeError(type(n))

        return js_type, js_line


    def get_func_decl(self, n, typedef=None, decl=None, ptr_decl=None) -> JsTypeLine:
        js_type: CType = None
        js_line: str = '/* unset */'
        js_name: str | None = None

        assert isinstance(n.args, c_ast.ParamList)
        assert isinstance(n.args.params, list)
        typedef_js_name: str | None = None
        decl_js_name: str | None = None

        if typedef:
            typedef_js_name = typedef.name
            decl_js_name = n.type.declname
            js_name = decl_js_name
        elif decl:
            decl_js_name = decl.name
            js_name = decl_js_name

        js_type = {
            'kind': 'FuncDecl',
            'name': js_name,
            'return_type': None,
            'params_types': [],
        }

        # return type
        t, _ = self.get_node(n.type, typedef=typedef, func_decl=n, ptr_decl=ptr_decl)
        js_type['return_type'] = t

        # params types
        for m in n.args.params:
            t, _ = self.get_node(m, func_decl=n)
            js_type['params_types'].append(t)

        if not ptr_decl and typedef_js_name:
            self.USER_DEFINED_TYPEDEF_FUNC_DECL[typedef_js_name] = js_type

        if not typedef and not ptr_decl and decl_js_name:
            self.USER_DEFINED_FUNC_DECL[decl_js_name] = js_type

        js_line = f'func_decl: {dumps(js_type)}'
        return js_type, js_line


    # def get_enum_decl(self, n) -> JsTypeLine:
    #     js_type: CType
    #     js_line: str
    #     raise TypeError(type(n))
    #     return js_type, js_line


    # def get_array_decl(n) -> JsTypeLine:
    #     js_type: CType
    #     js_line: str
    #     raise TypeError(type(n.type))
    #     return js_type, js_line


    def get_typedef(self, n) -> JsTypeLine:
        js_type: CType
        js_line: str = '/* unset */'
        js_name: str = n.name

        if isinstance(n.type, c_ast.TypeDecl):
            t, _ = self.get_type_decl(n.type, typedef=n)
        elif isinstance(n.type, c_ast.FuncDecl):
            t, _ = self.get_func_decl(n.type, typedef=n)
        elif isinstance(n.type, c_ast.PtrDecl):
            t, _ = self.get_ptr_decl(n.type, typedef=n)
        else:
            raise TypeError(type(n.type))

        js_type = {
            'kind': 'Typedef',
            'name': js_name,
            'type': t,
        }

        js_line = f'typedef: {dumps(js_type)}'
        return js_type, js_line


    def get_decl(self, n, func_decl=None) -> JsTypeLine:
        js_type: CType = None
        js_line: str = '/* unset */'

        if isinstance(n.type, c_ast.Enum):
            js_type, js_line = self.get_enum(n.type, decl=n)
        elif isinstance(n.type, c_ast.TypeDecl):
            js_type, js_line = self.get_type_decl(n.type, decl=n)
        elif isinstance(n.type, c_ast.FuncDecl):
            js_type, js_line = self.get_func_decl(n.type, decl=n)
        elif isinstance(n.type, c_ast.PtrDecl):
            js_type, js_line = self.get_ptr_decl(n.type, decl=n)
        else:
            raise TypeError(type(n.type))
        
        return js_type, js_line


    def get_node(self, n, typedef=None, decl=None, ptr_decl=None, func_decl=None) -> JsTypeLine:
        # NOTE: typedef unused
        js_type: CType = None
        js_line: str = '/* unset */'

        if isinstance(n, c_ast.Decl):
            js_type, js_line = self.get_decl(n, func_decl=func_decl)
        elif isinstance(n, c_ast.TypeDecl):
            js_type, js_line = self.get_type_decl(n, decl=decl, func_decl=func_decl)
        elif isinstance(n, c_ast.PtrDecl):
            js_type, js_line = self.get_ptr_decl(n, decl=decl, func_decl=func_decl)
        elif isinstance(n, c_ast.FuncDecl):
            js_type, js_line = self.get_func_decl(n, typedef=typedef, decl=decl, ptr_decl=ptr_decl)
        elif isinstance(n, c_ast.Typename):
            js_type, js_line = self.get_typename(n, decl=decl, func_decl=func_decl)
        else:
            raise TypeError(n)

        return js_type, js_line


    def get_file_ast(self, file_ast, shared_library: str) -> str:
        js_lines: list[str]
        js_type: CType = None
        js_line: str = '/* unset */'

        js_lines = [
            "import { CFunction, CCallback } from './quickjs-ffi.js';",
            f"const LIB = {dumps(shared_library)};",
            _QUICKJS_FFI_WRAP_PTR_FUNC_DECL,
        ]

        for n in file_ast.ext:
            print(n)

            if isinstance(n, c_ast.Typedef):
                js_type, js_line = self.get_typedef(n)
            elif isinstance(n, c_ast.Decl):
                js_type, js_line = self.get_decl(n)
            else:
                raise TypeError(type(n.type))

            js_lines.append(js_line)

        js_lines = '\n'.join(js_lines)
        return js_lines


    def create_output_dir(self, output_path: str):
        dirpath, filename = os.path.split(output_path)
        os.makedirs(dirpath, exist_ok=True)


    def preprocess_header_file(self, compiler: str, input_path: str, output_path: str):
        cmd = [compiler, '-E', input_path]
        output: bytes = subprocess.check_output(cmd)
        
        with open(output_path, 'w+b') as f:
            f.write(output)


    def optimize_type(self, js_type: Union[str, dict]) -> Union[str, dict]:
        output_js_type: str | dict

        if isinstance(js_type, dict) and js_type['kind'] == 'PtrDecl':
            if js_type['type'] == 'char':
                output_js_type = 'string'
            elif isinstance(js_type['type'], str) and js_type['type'] in self.USER_DEFINED_TYPEDEF_FUNC_DECL:
                js_name: str = js_type['type']
                output_js_type = deepcopy(self.USER_DEFINED_TYPEDEF_FUNC_DECL[js_name])

                output_js_type = {
                    'kind': 'PtrDecl',
                    'name': js_name,
                    'type': output_js_type,
                }
            else:
                output_js_type = 'pointer'
        elif isinstance(js_type, dict) and js_type['kind'] == 'Typename':
            output_js_type = self.optimize_type(js_type['type'])
        elif isinstance(js_type, str):
            output_js_type = self.PRIMITIVE_C_TYPES.get(js_type, js_type)
        else:
            output_js_type = js_type 

        return output_js_type


    def optimize_USER_DEFINED_TYPEDEF_FUNC_DECL(self):
        USER_DEFINED_TYPEDEF_FUNC_DECL = deepcopy(self.USER_DEFINED_TYPEDEF_FUNC_DECL)

        for js_name, js_type in USER_DEFINED_TYPEDEF_FUNC_DECL.items():
            js_type['return_type'] = self.optimize_type(js_type['return_type'])
            js_type['params_types'] = [self.optimize_type(n) for n in js_type['params_types']]
            self.USER_DEFINED_TYPEDEF_FUNC_DECL[js_name] = js_type


    def optimize_USER_DEFINED_FUNC_DECL(self):
        USER_DEFINED_FUNC_DECL = deepcopy(self.USER_DEFINED_FUNC_DECL)

        for js_name, js_type in USER_DEFINED_FUNC_DECL.items():
            js_type['return_type'] = self.optimize_type(js_type['return_type'])
            js_type['params_types'] = [self.optimize_type(n) for n in js_type['params_types']]
            self.USER_DEFINED_FUNC_DECL[js_name] = js_type


    def optmize_defs(self):
        self.optimize_USER_DEFINED_TYPEDEF_FUNC_DECL()
        self.optimize_USER_DEFINED_FUNC_DECL()


    def translate_to_js(self) -> str:
        self.optmize_defs()
        
        lines: list[str] = [
            "import { CFunction, CCallback } from './quickjs-ffi.js';",
        ]

        output: str = '\n'.join(lines)
        return output


    def translate(self, compiler: str, shared_library: str, input_path: str, output_path: str):
        # check existance of input_path
        assert os.path.exists(input_path)

        # create destination directory
        self.create_output_dir(output_path)

        # preprocess input header path
        dirpath, filename = os.path.split(output_path)
        basename, ext = os.path.splitext(filename)
        processed_output_path: str = os.path.join(dirpath, f'{basename}.h')
        self.preprocess_header_file(compiler, input_path, processed_output_path)

        # parse input header path
        file_ast = parse_file(processed_output_path, use_cpp=True)
        assert isinstance(file_ast, c_ast.FileAST)

        # wrap C code into JS
        self.get_file_ast(file_ast, shared_library=shared_library)
        output_data: str = self.translate_to_js()

        print('-' * 20)
        print(output_data)

        with open(output_path, 'w+') as f:
            f.write(output_data)

        self.print()


    def print(self):
        # pprint(TYPES, sort_dicts=False)
        print('USER_DEFINED_TYPE_DECL:')
        pprint(self.USER_DEFINED_TYPE_DECL, sort_dicts=False)
        print()

        print('USER_DEFINED_FUNC_DECL:')
        pprint(self.USER_DEFINED_FUNC_DECL, sort_dicts=False)
        print()
        
        print('USER_DEFINED_STRUCT_DECL:')
        pprint(self.USER_DEFINED_STRUCT_DECL, sort_dicts=False)
        print()

        print('USER_DEFINED_UNION_DECL:')
        pprint(self.USER_DEFINED_UNION_DECL, sort_dicts=False)
        print()

        print('USER_DEFINED_ENUM_DECL:')
        pprint(self.USER_DEFINED_ENUM_DECL, sort_dicts=False)
        print()
        
        print('USER_DEFINED_ARRAY_DECL:')
        pprint(self.USER_DEFINED_ARRAY_DECL, sort_dicts=False)
        print()
        
        print('USER_DEFINED_TYPEDEF_STRUCT:')
        pprint(self.USER_DEFINED_TYPEDEF_STRUCT, sort_dicts=False)
        print()

        print('USER_DEFINED_TYPEDEF_UNION:')
        pprint(self.USER_DEFINED_TYPEDEF_UNION, sort_dicts=False)
        print()

        print('USER_DEFINED_TYPEDEF_ENUM:')
        pprint(self.USER_DEFINED_TYPEDEF_ENUM, sort_dicts=False)
        print()
        
        print('USER_DEFINED_TYPEDEF_FUNC_DECL:')
        pprint(self.USER_DEFINED_TYPEDEF_FUNC_DECL, sort_dicts=False)
        print()
        
        print('USER_DEFINED_TYPEDEF_PTR_DECL:')
        pprint(self.USER_DEFINED_TYPEDEF_PTR_DECL, sort_dicts=False)
        print()

        


if __name__ == '__main__':
    # cli arg parser
    parser = argparse.ArgumentParser(description='Convert .h to .js')
    parser.add_argument('-c', dest='compiler', default='gcc', help='gcc, clang, tcc')
    parser.add_argument('-l', dest='shared_library', default='./libcfltk.so', help='Shared library')
    parser.add_argument('-i', dest='input_path', help='input .h path')
    parser.add_argument('-o', dest='output_path', help='output .js path')
    args = parser.parse_args()

    # translate
    c_parser = CParser()
    c_parser.translate(args.compiler, args.shared_library, args.input_path, args.output_path)
