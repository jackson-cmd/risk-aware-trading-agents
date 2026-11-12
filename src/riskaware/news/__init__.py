"""News collection (Finviz + RSS + SEC filings) and cross-source verification."""

from .verify import NewsReport, analyze_news, cluster_items

__all__ = ["NewsReport", "analyze_news", "cluster_items"]
