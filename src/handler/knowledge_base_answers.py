import logging

from pydantic_ai import Agent
from sqlmodel import select, cast, String

from models import Message, KBTopic
from utils.voyage_embed_text import voyage_embed_text
from .base_handler import BaseHandler

# Creating an object
logger = logging.getLogger(__name__)


class KnowledgeBaseAnswers(BaseHandler):
    async def __call__(self, message: Message):
        rephrased_agent = Agent(
            model="anthropic:claude-3-5-haiku-latest",
            system_prompt=f"""Phrase the following message as a short paragraph describing a query from the knowledge base.
            - Use English only!
            - Ensure only to include the query itself. The message that includes a lot of information - focus on what the user asks you.
            - Your name is @{(await self.whatsapp.get_my_jid()).user}
            - ONLY answer with the new phrased query, no other text!""",
        )

        # We obviously need to translate the question and turn the question vebality to a title / summary text to make it closer to the questions in the rag
        rephrased_response = await rephrased_agent.run(message.text)
        # Get query embedding
        embedded_question = (
            await voyage_embed_text(self.embedding_client, [rephrased_response.data])
        )[0]

        select_from = None
        if message.group:
            select_from = [message.group]
            if message.group.community_keys:
                select_from.extend(
                    await message.group.get_related_community_groups(self.session)
                )

        # query for user query
        q = (
            select(KBTopic)
            .order_by(KBTopic.embedding.l2_distance(embedded_question))
            .limit(5)
        )
        if select_from:
            q = q.where(
                cast(KBTopic.group_jid, String).in_(
                    [group.group_jid for group in select_from]
                )
            )
        retrieved_topics = await self.session.exec(q)

        similar_topics = []
        for result in retrieved_topics:
            similar_topics.append(f"{result.subject} \n {result.summary}")

        generation_agent = Agent(
            model="anthropic:claude-3-5-sonnet-latest",
            system_prompt="""Based on the topics attached, write a response to the query.
            - Write a casual direct response to the query. no need to repeat the query.
            - Answer in the same language as the query.
            - Only answer from the topics attached, no other text.
            - If the related topics are not relevant or not found, please let the user know.
            - When answering, provide a complete answer to the message - telling the user everything they need to know. BUT not too much! remember - it's a chat.
            - Please do tag users while talking about them (e.g., @972536150150). ONLY answer with the new phrased query, no other text.""",
        )

        prompt_template = f"""
        # Query:
        {message.text}
        
        # Related Topics:
        {"\n---\n".join(similar_topics) if len(similar_topics) > 0 else "No related topics found."}
        """

        generation_response = await generation_agent.run(prompt_template)
        logger.info(
            "RAG Query Results:\n"
            f"Question: {message.text}\n"
            f"Rephrased Question: {rephrased_response.data}\n"
            f"Chat JID: {message.chat_jid}\n"
            f"Retrieved Topics: {len(similar_topics)}\n"
            "Topics:\n"
            + "\n".join(f"- {topic[:100]}..." for topic in similar_topics)
            + "\n"
            f"Generated Response: {generation_response.data}"
        )
        await self.send_message(message.chat_jid, generation_response.data)
