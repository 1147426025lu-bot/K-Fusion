cmake_minimum_required(VERSION 3.10)
project(PLCCompiler)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# 寻找 LLVM 和 Clang 依赖
find_package(LLVM REQUIRED CONFIG)
find_package(Clang REQUIRED CONFIG)

include_directories(${LLVM_INCLUDE_DIRS} ${CLANG_INCLUDE_DIRS})
link_directories(${LLVM_LIBRARY_DIRS})
add_definitions(${LLVM_DEFINITIONS})

# 【核心修复】禁用 RTTI，以匹配 LLVM 默认编译选项
add_compile_options(-fno-rtti)

# 1. 编译主工具 plc_ast
add_executable(plc_ast frontend/ast/ast_tool__AST工具.cpp)
target_link_libraries(plc_ast PRIVATE
    clangTooling
    clangFrontend
    clangRewrite
    clangAST
    clangASTMatchers
    clangParse
    clangSema
    clangBasic
    clangLex
    clangSerialization
    ${LLVM_AVAILABLE_LIBS}
)

# 2. 编译后端 Pass 插件
add_library(PLCLowJitterPass MODULE backend/pass/PLCLowJitterPass__低抖动Pass.cpp)
target_compile_definitions(PLCLowJitterPass PRIVATE ${LLVM_DEFINITIONS})
set_target_properties(PLCLowJitterPass PROPERTIES 
    POSITION_INDEPENDENT_CODE ON
    C_VISIBILITY_PRESET default
    CXX_VISIBILITY_PRESET default
    PREFIX ""
)

# --- AI-PLC Fusion Compiler Pass ---
add_library(PLCFusionPass MODULE
    backend/pass/PLCFusionPass__内核化Pass.cpp
    backend/pass/PLCFusionFixedPoint__定点Pass.cpp)
set_target_properties(PLCFusionPass PROPERTIES
    CXX_STANDARD 17
    PREFIX "" 
)
