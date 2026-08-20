"""事件类型常量（Q1）：集中定义，避免魔法字符串散落在各智能体服务中。"""

# 采集请求：由调度器 / CLI / HTML 手动提交触发，采集智能体消费
EVENT_CRAWL_REQUESTED = "crawl.requested"
# 文章采集完成：采集智能体产出，处理智能体消费
EVENT_ARTICLE_CRAWLED = "article.crawled"
# 摘要生成完成：处理智能体产出，适配智能体消费
EVENT_SUMMARY_GENERATED = "summary.generated"
# 平台文案适配完成：适配智能体产出，审核智能体消费
EVENT_COPY_ADAPTED = "copy.adapted"
# 审核通过（进入待发布）：审核智能体产出，终态确认
EVENT_REVIEW_PASSED = "review.passed"
