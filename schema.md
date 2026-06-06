##生成样例

title: 茉莉与弹珠   // 剧本名称
characters:
  - name: 安子    //出场人物
    description: 卖弹珠的女孩，扎油麻花辫，裙有油迹，倔强敏感    //人物简介
  - name: 老马
    description: 黄包车夫，老实木讷
scenes:         // 场景列表
  - scene_id: 1    // 场景编号
    location: 桥边  //场景地点
    time: 傍晚      //场景时间
    description: 桥头圆柱雕龙，青苔爬满，水汽氤氲。   //场景氛围和视觉提示
    elements:   // 场景元素，按顺序描述动作、对白和旁白
      - type: action  //元素类型：动作、对白、旁白
        content: 安子抱着盒子坐在桥头，看着河面花灯。  //动作内容
      - type: line  
        speaker: 老马  //台词说话人
        text: 安子，这几天爸爸多拉点人给你换条裙子。   //台词内容
        action: 笑着把钱放进盒子   //台词附加动作
        emotion: 慈爱           //台词附加情绪
      - type: narrate
        content: 安子心里想要，但只是低头抿嘴。  //旁白内容，通常是角色内心独白或画外音
        voice: 内心独白                     //旁白语气提示（可选，指导演员表演）

##Schema 设计原因
分场结构：剧本天然以“场景”为单位，scenes 数组配合 scene_id 便于快速定位、修改和重组。

角色独立管理：将角色抽取到顶层 characters，避免每个台词重复描述，也方便后续做角色线分析或分配演员。

元素序列化：elements 数组按顺序混合 action / line / narrate，真实反映电影剧本的“镜头-对白-画外音”交替节奏。

精简旁白：narrate 仅在必要时使用，保持剧本可执行性（导演和演员主要依赖动作和对白）。

情绪与动作标注：为每句对白附加 action 和 emotion，帮助演员理解角色状态，这是专业剧本的常见做法。