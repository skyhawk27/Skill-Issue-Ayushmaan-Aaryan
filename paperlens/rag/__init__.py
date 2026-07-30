# PaperLens RAG module — retrieval-augmented chat over research papers
from rag.embeddings import build_index
from rag.chat import ask_question

__all__ = ["build_index", "ask_question"]
