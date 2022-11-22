# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.
from abc import ABC
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Union
from torchgen.model import (
    Argument,
    SelfArgument,
    TensorOptionsArguments,
)

# An ArgName is just the str name of the argument in schema;
# but in some special circumstances, we may add a little extra
# context.  The Enum SpecialArgName covers all of these cases;
# grep for their construction sites to see when they can occr.

SpecialArgName = Enum("SpecialArgName", ("possibly_redundant_memory_format",))
ArgName = Union[str, SpecialArgName]

# This class shouldn't be created directly; instead, use/create one of the singletons below.
@dataclass(frozen=True)
class BaseCppType:
    ns: Optional[str]
    name: str

    def __str__(self) -> str:
        if self.ns is None or self.ns == "":
            return self.name
        return f"{self.ns}::{self.name}"

class CType(ABC):
    def cpp_type(self, *, strip_ref: bool = False) -> str:
        raise NotImplementedError

    def cpp_type_registration_declarations(self) -> str:
        raise NotImplementedError

    def remove_const_ref(self) -> "CType":
        return self

@dataclass(frozen=True)
class BaseCType:
    type: BaseCppType

    def cpp_type(self, *, strip_ref: bool = False) -> str:
        return str(self.type)

    # For BC reasons, we don't want to introduce at:: namespaces to RegistrationDeclarations.yaml
    # TODO: Kill this when we eventually remove it!
    def cpp_type_registration_declarations(self) -> str:
        return str(self.type).replace("at::", "")

    def remove_const_ref(self) -> "CType":
        return self


@dataclass(frozen=True)
class ConstRefCType(CType):
    elem: "CType"

    def cpp_type(self, *, strip_ref: bool = False) -> str:
        if strip_ref:
            return self.elem.cpp_type(strip_ref=strip_ref)
        return f"const {self.elem.cpp_type()} &"

    def cpp_type_registration_declarations(self) -> str:
        return f"const {self.elem.cpp_type_registration_declarations()} &"

    def remove_const_ref(self) -> "CType":
        return self.elem.remove_const_ref()


@dataclass(frozen=True)
class MutRefCType(CType):
    elem: "CType"

    def cpp_type(self, *, strip_ref: bool = False) -> str:
        if strip_ref:
            return self.elem.cpp_type(strip_ref=strip_ref)
        return f"{self.elem.cpp_type()} &"

    def cpp_type_registration_declarations(self) -> str:
        return f"{self.elem.cpp_type_registration_declarations()} &"

    def remove_const_ref(self) -> "CType":
        return self.elem.remove_const_ref()

# A NamedCType is short for Named C++ semantic type.  A NamedCType represents a C++ type, plus
# semantic information about what it represents.  For example, consider the
# argument "bool pin_memory"; its normal C++ type is "bool", but its C++
# semantic type also keeps track that this represents a "pin_memory"; you can't
# just use a random other boolean in a context where you need a "pin_memory"!
#

@dataclass(frozen=True)
class NamedCType:
    name: ArgName
    type: CType

    def cpp_type(self, *, strip_ref: bool = False) -> str:
        return self.type.cpp_type(strip_ref=strip_ref)

    # For BC reasons, we don't want to introduce at:: namespaces to RegistrationDeclarations.yaml
    # TODO: Kill this when we eventually remove it!
    def cpp_type_registration_declarations(self) -> str:
        return self.type.cpp_type_registration_declarations()

    def remove_const_ref(self) -> "NamedCType":
        return NamedCType(self.name, self.type.remove_const_ref())

    def with_name(self, name: str) -> "NamedCType":
        return NamedCType(name, self.type)



# A binding represents any C++ binding site for a formal parameter.
# We don't distinguish between binding sites for different APIs;
# instead, all of the important distinctions are encoded in CType,
# which you can use to figure out if a given Binding is appropriate
# for use in another context.  (See torchgen.api.translate)


@dataclass(frozen=True)
class Binding:
    name: str
    nctype: NamedCType
    argument: Union[Argument, TensorOptionsArguments, SelfArgument]
    # TODO: maybe don't represent default here
    default: Optional[str] = None

    def rename(self, name: str) -> "Binding":
        return Binding(
            name=name,
            nctype=self.nctype,
            argument=self.argument,
            default=self.default,
        )

    @property
    def type(self) -> str:
        return self.nctype.cpp_type()

    def no_default(self) -> "Binding":
        return Binding(
            name=self.name,
            nctype=self.nctype,
            default=None,
            argument=self.argument,
        )

    def decl(self, *, func_ptr_cast: bool = False) -> str:
        mb_default = ""
        if self.default is not None:
            mb_default = f"={self.default}"

        # casting only needs to know the type
        if func_ptr_cast:
            return f"{self.type}"
        else:
            return f"{self.type} {self.name}{mb_default}"

    # For BC reasons, we don't want to introduce at:: namespaces to RegistrationDeclarations.yaml
    # TODO: Kill this when we eventually remove it!
    def decl_registration_declarations(self) -> str:
        type_s = self.nctype.cpp_type_registration_declarations()
        mb_default = ""
        if self.default is not None:
            mb_default = f"={self.default}"
        return f"{type_s} {self.name}{mb_default}"

    def defn(self) -> str:
        return f"{self.type} {self.name}"

    def with_name(self, name: str) -> "Binding":
        return Binding(
            name=name, nctype=self.nctype, argument=self.argument, default=self.default
        )


# An Expr is a C++ expression.  It has a C++ string representing its syntax,
# as well as a CType saying what it provides.


@dataclass(frozen=True)
class Expr:
    expr: str
    type: NamedCType
