# -*- coding: utf-8 -*-
"""周期体生成（两个后端共用的那一份）。

**为什么必须共用**
Linux 与 NuttX 两个后端如果各自生成"周期体"，两边的阶段顺序/调用表达式
迟早会分叉，于是"同一方案的两个部署"就不再可比，实验结论也就没意义。
所以周期体只在这里生成一次，两个后端都 #include 它。

周期体里**只有阶段调用**：没有 sleep、没有 timer、没有线程、没有 stdio。
调度（何时释放、用什么唤醒）是宿主的事——这条边界是 PR9-C 的核心要求。
"""

_H = """/* ecos_fused_body.h —— 由 ECoS 融合方案生成，两个后端共用；不要手改。
 *
 * 方案：%(groups)d 组 / %(stages)d 阶段，周期 %(period)s µs
 * 生成依据：plan.verification.ok=%(verified)s，执行时间证据级别 %(evidence)s
 *
 * 这里只有"一拍要做的计算"，没有调度原语。
 */
#ifndef ECOS_FUSED_BODY_H
#define ECOS_FUSED_BODY_H

%(bodies_decl)s
#endif /* ECOS_FUSED_BODY_H */
"""

_C = """/* ecos_fused_body.c —— 由 ECoS 融合方案生成，两个后端共用；不要手改。
 *
 * 组内顺序 = 方案顺序 = DAG 拓扑序（已由 PlanVerifier 校验）。
 * 每个 ecos_body_gN() 对应方案里的一次释放：释放 N 次/周期，而不是阶段数次。
 */
#include "ecos_fused_body.h"

%(protos)s

%(bodies)s"""


def emit_body_h(ir):
    decls = []
    for g in ir.groups:
        decls.append("void %s(void);" % g.body)
    return _H % {
        "groups": len(ir.groups),
        "stages": len(ir.order),
        "period": ("%.0f" % ir.period_us) if ir.period_us else "?",
        "verified": "true" if ir.verified else "false（未验证，产物只能用于对照）",
        "evidence": ir.evidence,
        "bodies_decl": "\n".join(decls),
    }


def emit_body_c(ir, protos):
    blocks = []
    for g in ir.groups:
        lines = []
        lines.append("/* 组 %s：释放相位 %s µs，执行 ≈%.2f µs（%d 阶段） */"
                     % (g.gid,
                        ("%.0f" % g.phase_us) if g.phase_us is not None else "?",
                        g.exec_us, len(g.stages)))
        lines.append("void %s(void)" % g.body)
        lines.append("{")
        for s in g.stages:
            lines.append("\t%s;\t/* %s，相位 %s µs */"
                         % (s.call, s.name,
                            ("%.0f" % s.phase_us) if s.phase_us is not None else "?"))
        lines.append("}")
        blocks.append("\n".join(lines))
    return _C % {"protos": "\n".join(protos), "bodies": "\n\n".join(blocks) + "\n"}
