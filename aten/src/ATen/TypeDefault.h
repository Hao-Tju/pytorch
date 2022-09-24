#pragma once

#include <ATen/Dimname.h>
#include <c10/core/MemoryFormat.h>
#include <c10/core/QScheme.h>
#include <ATen/core/Scalar.h>
#include <c10/core/TensorOptions.h>
#include <c10/macros/Export.h>
#include <c10/util/ArrayRef.h>
#include <c10/util/intrusive_ptr.h>

namespace c10 {
struct Storage;
}

namespace at {

class Tensor;
using TensorList = ArrayRef<Tensor>;

class Context;
struct Generator;

struct Quantizer;
// This is temporary typedef to enable Quantizer in aten native function API
// we'll remove them when we are actually exposing Quantizer class
// to frontend
using ConstQuantizerPtr = const c10::intrusive_ptr<Quantizer>&;

} // namespace at
