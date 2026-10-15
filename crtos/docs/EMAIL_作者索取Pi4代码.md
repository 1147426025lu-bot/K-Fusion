# 给 cRTOS 作者的索取邮件（草稿 v2，审阅后发出）

**收件人**：

- Chung-Fan Yang（Fixstars）——NuttX Workshop 2020 资料显示邮箱 `chungfan.yang@fixstars.com`（发送前请以 IEEE Access 2025 论文作者栏为准核对）
- Yasushi Shinjo（University of Tsukuba）——请以论文页脚或实验室主页核对邮箱

**主题**：Request for the Raspberry Pi 4B cRTOS source code (academic research)

---

Dear Dr. Yang and Dr. Shinjo,

I am a student working on real-time systems on commodity ARM64 boards. Your work has been  
a key reference for my study: I have carefully read your VEE 2020 paper ("Obtaining hard  
real-time performance and rich Linux features in a compounded real-time operating system by  
a partitioning hypervisor") as well as your 2025 IEEE Access paper on compounded RTOS  
implementations for the Raspberry Pi 4B.

As part of my research, I have set up a Raspberry Pi 4B Jailhouse environment (Linux  
5.15.86, Jailhouse v0.12, based on the jailhouse-images project), verified the hypervisor  
on the board, and successfully created and run a non-root cell on an isolated CPU. I am now  
trying to build a cRTOS-style setup on this board as a baseline for my research, since your  
Pi 4B implementation is the most authoritative reference I could find.

I am therefore writing to humbly ask whether you would be willing to share the source code  
of the Raspberry Pi 4B cRTOS described in your IEEE Access paper — in particular the  
NuttX-based sRTOS adaptation that runs as a Jailhouse inmate, and (if available) the  
remote-system-call layer. If the full code cannot be shared, even partial materials (such  
as the cell configurations, the NuttX board patches, or a description of the  
remote-system-call protocol) would be of great help to me.

If the code is shared, I promise that it will be used for academic research only, and that  
your papers will be cited as the origin of the implementation, with explicit acknowledgment  
of the code provenance in any resulting publication.

Thank you very much for your time, and for the excellent work that made this research  
direction possible.

Best regards,  
Changshun Lu (陆长顺)  
Shenyang University of Technology  
Email: <20020830@smail.sut.edu.cn>

---

## 发送前核对清单

1. **邮箱地址**以 IEEE Access 2025 论文作者通讯栏为准（Yang 的地址来自其 2020 年 NuttX Workshop 幻灯片，可信度较高；Shinjo 的请到论文/实验室主页核对）。
2. ~~填写姓名占位~~ 已填：Changshun Lu / Shenyang University of Technology / <20020830@smail.sut.edu.cn>；**不建议附仓库链接**（会暴露研究思路），如想增加可信度可附一张板上 cell 运行的截图。
3. 若一至两周无回复，可在 Fixstars 官网联系页或 Shinjo 实验室页面补发一次。
4. 并行提醒：**无论是否拿到代码，NuttX inmate 移植都要做**（拿到代码可省 remote-syscall 层与适配调试）。

## 相比 v1 的修改（按你的要求）

- 删除了合作署名（co-authorship）提议；
- 删除了 K-Fusion 项目名、仓库链接，以及"编译器内核化 vs 分区"的思路描述——现在只说"研究需要基线"；
- 语气整体放低（humbly ask / a student working on...），承诺部分只保留"仅学术使用 + 引用致谢"。
