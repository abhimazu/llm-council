import { useState, useEffect } from 'react';
import Sidebar from './components/Sidebar';
import ChatInterface from './components/ChatInterface';
import { api } from './api';
import './App.css';

function App() {
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);
  const [currentConversation, setCurrentConversation] = useState(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    loadConversations();
  }, []);

  useEffect(() => {
    if (currentConversationId) {
      loadConversation(currentConversationId);
    }
  }, [currentConversationId]);

  const loadConversations = async () => {
    try {
      const convs = await api.listConversations();
      setConversations(convs);
    } catch (error) {
      console.error('Failed to load conversations:', error);
    }
  };

  const loadConversation = async (id) => {
    try {
      const conv = await api.getConversation(id);
      setCurrentConversation(conv);
    } catch (error) {
      console.error('Failed to load conversation:', error);
    }
  };

  const handleNewConversation = async () => {
    try {
      const newConv = await api.createConversation();
      setConversations([
        {
          id: newConv.id,
          created_at: newConv.created_at,
          title: newConv.title || 'New Conversation',
          message_count: 0,
        },
        ...conversations,
      ]);
      setCurrentConversationId(newConv.id);
    } catch (error) {
      console.error('Failed to create conversation:', error);
    }
  };

  const handleSelectConversation = (id) => {
    setCurrentConversationId(id);
  };

  /**
   * Mutate the last assistant message in the current conversation.
   * Wraps the common state-update boilerplate so each event handler
   * doesn't repeat the spread + slice dance.
   */
  const updateLastAssistant = (mutator) => {
    setCurrentConversation((prev) => {
      if (!prev || !prev.messages || prev.messages.length === 0) return prev;
      const messages = [...prev.messages];
      const lastIdx = messages.length - 1;
      messages[lastIdx] = { ...messages[lastIdx] };
      mutator(messages[lastIdx]);
      return { ...prev, messages };
    });
  };

  const handleSendMessage = async (content) => {
    if (!currentConversationId) return;

    setIsLoading(true);
    // Tag the optimistic message so we can locate it on error rollback,
    // instead of slicing by hardcoded position (-2).
    const optimisticToken = `pending-${Date.now()}`;

    try {
      const userMessage = { role: 'user', content };
      const assistantMessage = {
        role: 'assistant',
        stage1: null,
        stage2: null,
        stage3: null,
        metadata: null,
        loading: { stage1: false, stage2: false, stage3: false },
        streamError: null,
        cacheInfo: null,
        routingDecision: null,
        soloModel: null,
        _pendingToken: optimisticToken,
      };

      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, userMessage, assistantMessage],
      }));

      await api.sendMessageStream(currentConversationId, content, (eventType, event) => {
        switch (eventType) {
          case 'stage1_start':
            updateLastAssistant((m) => { m.loading = { ...m.loading, stage1: true }; });
            break;

          case 'stage1_complete':
            updateLastAssistant((m) => {
              m.stage1 = event.data;
              m.loading = { ...m.loading, stage1: false };
            });
            break;

          case 'stage2_start':
            updateLastAssistant((m) => { m.loading = { ...m.loading, stage2: true }; });
            break;

          case 'stage2_complete':
            updateLastAssistant((m) => {
              m.stage2 = event.data;
              m.metadata = event.metadata;
              m.loading = { ...m.loading, stage2: false };
            });
            break;

          case 'stage3_start':
            updateLastAssistant((m) => { m.loading = { ...m.loading, stage3: true }; });
            break;

          case 'stage3_complete':
            updateLastAssistant((m) => {
              m.stage3 = event.data;
              m.loading = { ...m.loading, stage3: false };
            });
            break;

          case 'title_complete':
            // Reload conversations so sidebar picks up the new title.
            loadConversations();
            break;

          case 'title_failed':
            // Title-gen quietly failed — sidebar handles null titles already.
            // We refresh so the sidebar shows the placeholder explicitly.
            loadConversations();
            break;

          case 'complete':
            loadConversations();
            setIsLoading(false);
            break;

          case 'error':
            // Server emitted a fatal error mid-stream. Stash on the
            // assistant message so ChatInterface can surface it.
            updateLastAssistant((m) => {
              m.streamError = {
                kind: event.kind || 'server_error',
                detail: event.detail || 'An internal error occurred.',
                requestId: event.request_id || null,
              };
              m.loading = { stage1: false, stage2: false, stage3: false };
            });
            setIsLoading(false);
            break;

          case 'cache_hit':
            updateLastAssistant((m) => {
              m.cacheInfo = { hit: true };
            });
            break;

          case 'routing_decision':
            updateLastAssistant((m) => {
              m.routingDecision = {
                useCouncil: !!event.use_council,
                reason: event.reason || 'unknown',
                classifierUsed: !!event.classifier_used,
              };
            });
            break;

          case 'solo_start':
            updateLastAssistant((m) => {
              m.loading = { ...m.loading, stage3: true };
              m.soloModel = event.model || null;
            });
            break;

          case 'solo_complete':
            // The backend also emits stage3_complete with the same
            // payload, so stage3 will land via that case. Nothing to
            // do here beyond acknowledging.
            break;

          default:
            console.log('Unknown event type:', eventType, event);
        }
      });
    } catch (error) {
      console.error('Failed to send message:', error);
      // Rollback only the optimistic messages, not anything else that
      // may have arrived concurrently.
      setCurrentConversation((prev) => {
        if (!prev) return prev;
        const messages = prev.messages.filter(
          (m) => m._pendingToken !== optimisticToken
        );
        // Also drop the user message that was added in the same call.
        // We identify it by being immediately before the pending one
        // in the original sequence; since we filtered above, locate
        // the most recent user message and drop that too.
        if (messages.length && messages[messages.length - 1]?.role === 'user'
            && messages[messages.length - 1].content === content) {
          messages.pop();
        }
        return { ...prev, messages };
      });
      setIsLoading(false);
    }
  };

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
      />
      <ChatInterface
        conversation={currentConversation}
        onSendMessage={handleSendMessage}
        isLoading={isLoading}
      />
    </div>
  );
}

export default App;
