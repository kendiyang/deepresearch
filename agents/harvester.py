from modules.state import ResearchState
from services.search.dorks import get_dork_generator
from services.search.search import get_search_service

async def harvester(state: ResearchState):
    """
    从ResearchState中提取主题信息
    :param state: ResearchState
    :return: 提取的主题列表
    """
    topics = state.get('intent_topics', [])

    dorks = get_dork_generator()
    search =  get_search_service()

    for topic in topics:
       try:
           dorks = await dorks.run(topic)
           results = await search.find_discussion_urls(dorks,per_dork=50)
               
       except ValueError as e:
           print(f"Error generating dorks for topic '{topic}': {e}")
    return state['intent_topics']