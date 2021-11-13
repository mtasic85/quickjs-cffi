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
            if (type.kind == 'PtrDecl') {
                if (type.type == 'char') {
                    return 'string';
                } else {
                    return 'pointer';
                }
            } else if (type.kind == 'PtrFuncDecl') {
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
                if (type.kind == 'PtrFuncDecl') {
                    const c_cb = new CCallback(js_arg, null, ...[type.return_type, ...type.params_types]);
                    return c_cb.cfuncptr;
                } else {
                    return js_arg;
                }
            } else {
                return js_arg;
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


class CParser:
    BUILTIN_TYPES_NAMES = [
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

    BUILTIN_TYPES = {
        **{n: n for n in BUILTIN_TYPES_NAMES},
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


    def __init__(self, compiler: str, shared_library: str, input_path: str, output_path: str):
        self.compiler = compiler
        self.shared_library = shared_library
        self.input_path = input_path
        self.output_path = output_path

        self.TYPE_DECL = {}
        self.FUNC_DECL = {}
        self.STRUCT_DECL = {}
        self.UNION_DECL = {}
        self.ENUM_DECL = {}
        self.ARRAY_DECL = {}

        self.TYPEDEF_STRUCT = {}
        self.TYPEDEF_UNION = {}
        self.TYPEDEF_ENUM = {}
        self.TYPEDEF_FUNC_DECL = {}
        self.TYPEDEF_PTR_DECL = {}

        self.SIMPLIFIED_FUNC_DECL = {}
        self.SIMPLIFIED_TYPEDEF_FUNC_DECL = {}
        self.SIMPLIFIED_TYPEDEF_PTR_DECL = {}


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


    def get_typename(self, n, decl=None, func_decl=None) -> CType:
        js_type: CType = None
        js_name: str | None = None

        if decl:
            raise TypeError(type(n))
        elif func_decl:
            js_name = n.name
            t = self.get_node(n.type, func_decl=func_decl)

            js_type = {
                'kind': 'Typename',
                'name': js_name,
                'type': t,
            }
        else:
            raise TypeError(type(n))

        return js_type


    def get_type_decl(self, n, typedef=None, decl=None, func_decl=None) -> CType:
        js_type: CType = None
        js_name: str | None = None

        if typedef:
            js_name = typedef.name

            if isinstance(n.type, c_ast.Struct):
                t = self.get_struct(n.type, typedef=typedef, type_decl=n)
                
                js_type = {
                    'kind': 'TypeDecl',
                    'name': js_name,
                    'type': t,
                }

                if js_name:
                    self.TYPEDEF_STRUCT[js_name] = js_type
            elif isinstance(n.type, c_ast.Union):
                t = self.get_union(n.type, typedef=typedef, type_decl=n)
                
                js_type = {
                    'kind': 'TypeDecl',
                    'name': js_name,
                    'type': t,
                }

                if js_name:
                    self.TYPEDEF_UNION[js_name] = js_type
            else:
                raise TypeError(n)
        elif decl or func_decl:
            if isinstance(n.type, c_ast.Enum):
                t = self.get_enum(n.type, type_decl=n)
                js_name = n.declname

                js_type = {
                    'kind': 'TypeDecl',
                    'name': js_name,
                    'type': t,
                }

                if js_name:
                    self.ENUM_DECL[js_name] = js_type
            elif isinstance(n.type, c_ast.PtrDecl):
                t = self.get_ptr_decl(n.type, decl=decl, func_decl=func_decl)
                js_name = decl.name

                js_type = {
                    'kind': 'TypeDecl',
                    'name': js_name,
                    'type': t,
                }
            elif isinstance(n.type, c_ast.IdentifierType):
                js_type = self.get_leaf_name(n.type) # str repo of type in C
            else:
                raise TypeError(n)
        else:
            raise TypeError(n)

        if js_name:
            self.TYPE_DECL[js_name] = js_type

        return js_type


    def get_ptr_decl(self, n, typedef=None, decl=None, func_decl=None) -> CType:
        js_type: CType = None
        js_name: str | None = None

        if typedef:
            t = self.get_node(n.type, typedef=typedef, ptr_decl=n)
            js_name = typedef.name

            js_type = {
                'kind': 'PtrDecl',
                'name': js_name,
                'type': t,
            }

            if js_name:
                self.TYPEDEF_PTR_DECL[js_name] = js_type
        elif decl:
            t = self.get_node(n.type, decl=decl, ptr_decl=n)
            js_name = None # NOTE: in this implementation is always None, but can be set to real name

            js_type = {
                'kind': 'PtrDecl',
                'name': js_name,
                'type': t,
            }
        elif func_decl:
            t = self.get_node(n.type, func_decl=func_decl, ptr_decl=n)
            js_name = None # NOTE: in this implementation is always None, but can be set to real name

            js_type = {
                'kind': 'PtrDecl',
                'name': js_name,
                'type': t,
            }
        else:
            raise TypeError(type(n))
        
        return js_type


    def get_struct(self, n, typedef=None, type_decl=None) -> CType:
        js_type: CType = None
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
            self.STRUCT_DECL[js_name] = js_type

        return js_type


    def get_union(self, n, typedef=None, type_decl=None) -> CType:
        js_type: CType = None
        js_name: str
        js_fields: dict
        
        if n.name:
            js_name = n.name
        elif type_decl and type_decl.declname:
            js_name = type_decl.declname
        elif typedef and typedef.name:
            js_name = typedef.name
        else:
            raise ValueError(f'Could not get name of union node {n}')

        # NOTE: does not parse struct fields
        js_fields = {}

        js_type = {
            'kind': 'Union',
            'name': js_name,
            'fields': js_fields,
        }

        if js_name:
            self.UNION_DECL[js_name] = js_type

        return js_type


    def get_enum(self, n, decl=None, type_decl=None) -> CType:
        js_type: CType
        
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

            self.ENUM_DECL[js_type["name"]] = js_type
        else:
            raise TypeError(type(n))

        return js_type


    def get_func_decl(self, n, typedef=None, decl=None, ptr_decl=None) -> CType:
        js_type: CType = None
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
        t = self.get_node(n.type, typedef=typedef, func_decl=n, ptr_decl=ptr_decl)
        js_type['return_type'] = t

        # params types
        for m in n.args.params:
            t = self.get_node(m, func_decl=n)
            js_type['params_types'].append(t)

        if not ptr_decl and typedef_js_name:
            self.TYPEDEF_FUNC_DECL[typedef_js_name] = js_type

        if not typedef and not ptr_decl and decl_js_name:
            self.FUNC_DECL[decl_js_name] = js_type

        return js_type


    # def get_enum_decl(self, n) -> CType:
    #     js_type: CType
    #     raise TypeError(type(n))
    #     return js_type


    def get_array_decl(self, n, decl=None) -> CType:
        # FIXME: implement
        js_type: CType = None
        return js_type


    def get_typedef(self, n) -> CType:
        js_type: CType
        js_name: str = n.name

        if isinstance(n.type, c_ast.TypeDecl):
            t = self.get_type_decl(n.type, typedef=n)
        elif isinstance(n.type, c_ast.FuncDecl):
            t = self.get_func_decl(n.type, typedef=n)
        elif isinstance(n.type, c_ast.PtrDecl):
            t = self.get_ptr_decl(n.type, typedef=n)
        else:
            raise TypeError(type(n.type))

        js_type = {
            'kind': 'Typedef',
            'name': js_name,
            'type': t,
        }

        return js_type


    def get_decl(self, n, func_decl=None) -> CType:
        js_type: CType = None

        if isinstance(n.type, c_ast.Enum):
            js_type = self.get_enum(n.type, decl=n)
        elif isinstance(n.type, c_ast.TypeDecl):
            js_type = self.get_type_decl(n.type, decl=n)
        elif isinstance(n.type, c_ast.FuncDecl):
            js_type = self.get_func_decl(n.type, decl=n)
        elif isinstance(n.type, c_ast.PtrDecl):
            js_type = self.get_ptr_decl(n.type, decl=n)
        elif isinstance(n.type, c_ast.ArrayDecl):
            js_type = self.get_array_decl(n.type, decl=n)
        else:
            raise TypeError(type(n.type))
        
        return js_type


    def get_node(self, n, typedef=None, decl=None, ptr_decl=None, func_decl=None) -> CType:
        # NOTE: typedef unused
        js_type: CType = None

        if isinstance(n, c_ast.Decl):
            js_type = self.get_decl(n, func_decl=func_decl)
        elif isinstance(n, c_ast.TypeDecl):
            js_type = self.get_type_decl(n, decl=decl, func_decl=func_decl)
        elif isinstance(n, c_ast.PtrDecl):
            js_type = self.get_ptr_decl(n, decl=decl, func_decl=func_decl)
        elif isinstance(n, c_ast.FuncDecl):
            js_type = self.get_func_decl(n, typedef=typedef, decl=decl, ptr_decl=ptr_decl)
        elif isinstance(n, c_ast.Typename):
            js_type = self.get_typename(n, decl=decl, func_decl=func_decl)
        else:
            raise TypeError(n)

        return js_type


    def get_file_ast(self, file_ast, shared_library: str):
        js_type: CType = None

        for n in file_ast.ext:
            print(n)

            if isinstance(n, c_ast.Typedef):
                js_type = self.get_typedef(n)
            elif isinstance(n, c_ast.Decl):
                js_type = self.get_decl(n)
            else:
                raise TypeError(type(n.type))


    def create_output_dir(self, output_path: str):
        dirpath, filename = os.path.split(output_path)
        os.makedirs(dirpath, exist_ok=True)


    def preprocess_header_file(self, compiler: str, input_path: str, output_path: str):
        cmd = [compiler, '-E', input_path]
        output: bytes = subprocess.check_output(cmd)
        
        with open(output_path, 'w+b') as f:
            f.write(output)


    def simplify_type(self, js_type: Union[str, dict]) -> CType:
        output_js_type: CType

        if isinstance(js_type, dict) and js_type['kind'] == 'PtrDecl':
            if js_type['type'] == 'char':
                output_js_type = 'string'
            else:
                output_js_type = 'pointer'
        elif isinstance(js_type, dict) and js_type['kind'] == 'Typename':
            output_js_type = self.simplify_type(js_type['type'])
        elif isinstance(js_type, str):
            if js_type in self.BUILTIN_TYPES:
                output_js_type = self.BUILTIN_TYPES[js_type]
            elif js_type in self.TYPEDEF_PTR_DECL:
                output_js_type = 'pointer'
            else:
                output_js_type = js_type
        else:
            output_js_type = js_type 

        return output_js_type


    def simplify_FUNC_DECL(self):
        FUNC_DECL = deepcopy(self.FUNC_DECL)

        for js_name, js_type in FUNC_DECL.items():
            js_type = deepcopy(js_type)
            js_type['return_type'] = self.simplify_type(js_type['return_type'])
            js_type['params_types'] = [self.simplify_type(n) for n in js_type['params_types']]
            self.SIMPLIFIED_FUNC_DECL[js_name] = js_type


    def simplify_TYPEDEF_FUNC_DECL(self):
        TYPEDEF_FUNC_DECL = deepcopy(self.TYPEDEF_FUNC_DECL)

        for js_name, js_type in TYPEDEF_FUNC_DECL.items():
            js_type = deepcopy(js_type)
            js_type['return_type'] = self.simplify_type(js_type['return_type'])
            js_type['params_types'] = [self.simplify_type(n) for n in js_type['params_types']]
            self.TYPEDEF_FUNC_DECL[js_name] = js_type # FIXME: check simplify_FUNC_DECL


    def simplify_TYPEDEF_PTR_DECL(self):
        TYPEDEF_PTR_DECL = deepcopy(self.TYPEDEF_PTR_DECL)

        for js_name, js_type in TYPEDEF_PTR_DECL.items():
            js_type = deepcopy(js_type)
            js_type['type']['return_type'] = self.simplify_type(js_type['type']['return_type'])
            js_type['type']['params_types'] = [self.simplify_type(n) for n in js_type['type']['params_types']]
            self.TYPEDEF_PTR_DECL[js_name] = js_type # FIXME: check simplify_FUNC_DECL


    def simplify_types_defitions(self):
        self.simplify_TYPEDEF_FUNC_DECL()
        self.simplify_TYPEDEF_PTR_DECL()
        self.simplify_FUNC_DECL()


    def translate_to_js(self) -> str:
        self.simplify_types_defitions()
        
        lines: list[str] = [
            "import { CFunction, CCallback } from './quickjs-ffi.js';",
            f"const LIB = {dumps(self.shared_library)};",
            "const None = null; ",
            "",
            _QUICKJS_FFI_WRAP_PTR_FUNC_DECL,
            "",
        ]

        # TYPEDEF_ENUM
        for js_name, js_type in self.TYPEDEF_ENUM.items():
            line = f"/* TYPEDEF_ENUM: {js_type} */"
            lines.append(line)
        
        # ENUM_DECL
        for js_name, js_type in self.ENUM_DECL.items():
            if js_type['kind'] == 'Enum':
                line = f"export const {js_name} = {js_type['items']};"
            elif js_type['kind'] == 'TypeDecl':
                line = f"export const {js_name} = {js_type['type']['items']};"
            else:
                raise ValueError(js_type)

            lines.append(line)

        # TYPEDEF_FUNC_DECL
        for js_name, js_type in self.TYPEDEF_FUNC_DECL.items():
            line = f"/* TYPEDEF_FUNC_DECL: {js_type} */"
            lines.append(line)

        # TYPEDEF_PTR_DECL
        for js_name, js_type in self.TYPEDEF_PTR_DECL.items():
            line = f"/* TYPEDEF_PTR_DECL: {js_type} */"
            lines.append(line)

        # FUNC_DECL
        for js_name, js_type in self.FUNC_DECL.items():
            # simplified_js_type = self.SIMPLIFIED_FUNC_DECL[js_name]
            # simplified_return_type = simplified_js_type['return_type']
            # simplified_params_types = simplified_js_type['params_types']

            return_type = js_type['return_type']
            params_types = js_type['params_types']

            # func_return_type = simplified_return_type
            # func_params_types = simplified_params_types

            # cb_in_params = False

            # for pt in params_types:
            #     if not isinstance(pt, dict):
            #         continue
            #
            #     if pt['kind'] == 'Typename':
            #         pt = pt['type']
            #
            #     if not isinstance(pt, dict):
            #         continue
            #
            #     print('!', pt)
            #     if pt['kind'] == 'PtrDecl' and pt['type'] in self.TYPEDEF_FUNC_DECL:
            #         cb_in_params = True
            
            # params_types = [
            #     pt['type'] if isinstance(pt, dict) and pt['kind'] == 'Typename' else pt
            #     for pt in params_types
            # ]

            _params_types = []

            for pt in params_types:
                if isinstance(pt, dict) and pt['kind'] == 'Typename':
                    pt = pt['type']

                    if isinstance(pt, dict) and isinstance(pt['type'], str) and pt['type'] in self.TYPEDEF_FUNC_DECL:
                        typedef_func_decl = self.TYPEDEF_FUNC_DECL[pt['type']]
                        typedef_func_decl_return_type = self.simplify_type(typedef_func_decl['return_type'])
                        typedef_func_decl_params_types = self.simplify_type(typedef_func_decl['params_types'])

                        new_pt = {
                            'kind': 'PtrFuncDecl',
                            'return_type': typedef_func_decl_return_type,
                            'params_types': typedef_func_decl_params_types,
                        }

                        _params_types.append(new_pt)
                    else:
                        _params_types.append(pt)
                else:
                    _params_types.append(pt)

            params_types = _params_types

#             line = f"""
# // {js_name}      
# let _ffi_{js_name};

# try {{
#     _ffi_{js_name} = new CFunction(LIB, {dumps(js_name)}, null, {dumps(return_type)}, ...{params_types});
# }} catch (e) {{
#     console.log(e);
# }}
# """
#             lines.append(line)

            # if cb_in_params:
            #     types = [return_type, *params_types]
            #     line = f"export const {js_name} = _quickjs_ffi_wrap_ptr_func_decl(LIB, {dumps(js_name)}, null, ...{types});"
            # else:
            #     line = f"export const {js_name} = (...args) => _ffi_{js_name}.invoke(...args);"
            types = [return_type, *params_types]
            line = f"export const {js_name} = _quickjs_ffi_wrap_ptr_func_decl(LIB, {dumps(js_name)}, null, ...{types});"
            lines.append(line)

        output: str = '\n'.join(lines)
        return output


    def translate(self):
        # check existance of input_path
        assert os.path.exists(self.input_path)

        # create destination directory
        self.create_output_dir(self.output_path)

        # preprocess input header path
        dirpath, filename = os.path.split(self.output_path)
        basename, ext = os.path.splitext(filename)
        processed_output_path: str = os.path.join(dirpath, f'{basename}.h')
        self.preprocess_header_file(self.compiler, self.input_path, processed_output_path)

        # parse input header path
        file_ast = parse_file(processed_output_path, use_cpp=True)
        assert isinstance(file_ast, c_ast.FileAST)

        # wrap C code into JS
        self.get_file_ast(file_ast, shared_library=self.shared_library)
        output_data: str = self.translate_to_js()

        print('-' * 20)
        print(output_data)

        with open(self.output_path, 'w+') as f:
            f.write(output_data)

        self.print()


    def print(self):
        # pprint(TYPES, sort_dicts=False)
        print('TYPE_DECL:')
        pprint(self.TYPE_DECL, sort_dicts=False)
        print()

        print('FUNC_DECL:')
        pprint(self.FUNC_DECL, sort_dicts=False)
        print()
        
        print('STRUCT_DECL:')
        pprint(self.STRUCT_DECL, sort_dicts=False)
        print()

        print('UNION_DECL:')
        pprint(self.UNION_DECL, sort_dicts=False)
        print()

        print('ENUM_DECL:')
        pprint(self.ENUM_DECL, sort_dicts=False)
        print()
        
        print('ARRAY_DECL:')
        pprint(self.ARRAY_DECL, sort_dicts=False)
        print()
        
        print('TYPEDEF_STRUCT:')
        pprint(self.TYPEDEF_STRUCT, sort_dicts=False)
        print()

        print('TYPEDEF_UNION:')
        pprint(self.TYPEDEF_UNION, sort_dicts=False)
        print()

        print('TYPEDEF_ENUM:')
        pprint(self.TYPEDEF_ENUM, sort_dicts=False)
        print()
        
        print('TYPEDEF_FUNC_DECL:')
        pprint(self.TYPEDEF_FUNC_DECL, sort_dicts=False)
        print()
        
        print('TYPEDEF_PTR_DECL:')
        pprint(self.TYPEDEF_PTR_DECL, sort_dicts=False)
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
    c_parser = CParser(args.compiler, args.shared_library, args.input_path, args.output_path)
    c_parser.translate()
