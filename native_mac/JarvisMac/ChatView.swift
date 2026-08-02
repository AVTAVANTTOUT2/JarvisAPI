import SwiftUI

struct ChatView: View {
    @EnvironmentObject private var model: AppModel
    @State private var draft = ""
    @State private var speakResponse = false

    var body: some View {
        HStack(spacing: 0) {
            history
            Divider().opacity(0.4)
            conversation
        }
        .navigationTitle("Conversation")
    }

    private var history: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Conversations").font(.headline)
                Spacer()
                Button { model.newConversation() } label: { Image(systemName: "square.and.pencil") }
                    .buttonStyle(.plain)
                    .help("Nouvelle conversation")
            }
            .padding(.horizontal, 14)
            .padding(.top, 18)

            if model.snapshot.conversations.isEmpty {
                EmptyState(symbol: "bubble.left", title: "Aucun historique", subtitle: "Votre prochaine conversation apparaîtra ici.")
            } else {
                ScrollView {
                    LazyVStack(spacing: 5) {
                        ForEach(model.snapshot.conversations) { conversation in
                            Button {
                                Task { await model.openConversation(conversation) }
                            } label: {
                                VStack(alignment: .leading, spacing: 5) {
                                    HStack {
                                        Text(conversation.displayTitle)
                                            .font(.subheadline.weight(.medium))
                                            .lineLimit(1)
                                        Spacer()
                                        if conversation.pinned == 1 {
                                            Image(systemName: "pin.fill").font(.caption2).foregroundStyle(JarvisPalette.cyan)
                                        }
                                    }
                                    Text(conversation.lastMessage ?? "Nouvelle conversation")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(2)
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(10)
                                .background(
                                    model.activeConversationID == conversation.id
                                        ? JarvisPalette.blue.opacity(0.15)
                                        : .clear,
                                    in: RoundedRectangle(cornerRadius: 12)
                                )
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.horizontal, 8)
                }
            }
        }
        .frame(width: 245)
        .background(.black.opacity(0.025))
    }

    private var conversation: some View {
        VStack(spacing: 0) {
            if model.chatMessages.isEmpty { welcome }
            else { messageList }
            composer
        }
    }

    private var welcome: some View {
        VStack(spacing: 18) {
            Spacer()
            JarvisOrb(size: 82, active: model.socket.isConnected)
            VStack(spacing: 6) {
                Text("Que puis-je faire pour vous ?")
                    .font(.system(size: 28, weight: .semibold, design: .rounded))
                Text("Jarvis conserve le même contexte, la même mémoire et les mêmes capacités que sur le web.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 520)
            }
            HStack(spacing: 10) {
                suggestion("Prépare mon briefing", symbol: "sun.max")
                suggestion("Que dois-je prioriser ?", symbol: "scope")
                suggestion("Résume ma journée", symbol: "moon.stars")
            }
            Spacer()
        }
        .padding(30)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var messageList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 18) {
                    ForEach(model.chatMessages) { message in
                        MessageBubble(message: message)
                            .id(message.id)
                    }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(.horizontal, 28)
                .padding(.vertical, 22)
            }
            .scrollIndicators(.hidden)
            .onChange(of: model.chatMessages) { _, _ in
                withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo("bottom", anchor: .bottom) }
            }
        }
    }

    private var composer: some View {
        VStack(spacing: 9) {
            HStack {
                StatusPill(
                    text: model.audio.isRecording ? "Écoute…" : model.chatStatus,
                    color: model.audio.isRecording ? .red : model.socket.isConnected ? .green : .orange
                )
                Spacer()
                Toggle("Lire la réponse", isOn: $speakResponse)
                    .toggleStyle(.switch)
                    .controlSize(.small)
                    .font(.caption)
            }
            HStack(alignment: .bottom, spacing: 10) {
                TextField("Demander n’importe quoi…", text: $draft, axis: .vertical)
                    .textFieldStyle(.plain)
                    .lineLimit(1...5)
                    .font(.body)
                    .padding(.vertical, 9)
                    .onSubmit { send() }
                Button {
                    Task { await model.toggleVoiceRecording() }
                } label: {
                    Image(systemName: model.audio.isRecording ? "stop.fill" : "mic.fill")
                        .font(.body.weight(.semibold))
                        .foregroundStyle(model.audio.isRecording ? .white : .primary)
                        .frame(width: 34, height: 34)
                        .background(model.audio.isRecording ? .red : .clear, in: Circle())
                }
                .buttonStyle(.plain)
                .help(model.audio.isRecording ? "Arrêter et envoyer" : "Message vocal")
                Button(action: send) {
                    Image(systemName: "arrow.up")
                        .font(.body.weight(.bold))
                        .foregroundStyle(.white)
                        .frame(width: 34, height: 34)
                        .background(JarvisPalette.blue, in: Circle())
                }
                .buttonStyle(.plain)
                .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.isChatProcessing)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .jarvisGlass(cornerRadius: 18)
        }
        .padding(.horizontal, 24)
        .padding(.bottom, 20)
    }

    private func suggestion(_ text: String, symbol: String) -> some View {
        Button {
            draft = text
            send()
        } label: {
            Label(text, systemImage: symbol).padding(.horizontal, 4)
        }
        .buttonStyle(.bordered)
        .controlSize(.large)
    }

    private func send() {
        let content = draft
        draft = ""
        model.sendChat(content, speak: speakResponse)
    }
}

private struct MessageBubble: View {
    let message: ChatMessage

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            if message.role == .user { Spacer(minLength: 100) }
            if message.role != .user {
                Image(systemName: message.role == .system ? "exclamationmark.triangle.fill" : "sparkles")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(message.role == .system ? .orange : JarvisPalette.cyan)
                    .frame(width: 28, height: 28)
                    .background(.white.opacity(0.06), in: Circle())
            }
            VStack(alignment: .leading, spacing: 7) {
                if message.content.isEmpty && message.isStreaming {
                    HStack(spacing: 5) {
                        ProgressView().controlSize(.small)
                        Text("Jarvis réfléchit…").foregroundStyle(.secondary)
                    }
                } else {
                    Text(attributedContent)
                        .textSelection(.enabled)
                        .lineSpacing(3)
                }
                if message.isStreaming && !message.content.isEmpty {
                    HStack(spacing: 4) {
                        Circle().fill(JarvisPalette.cyan).frame(width: 5, height: 5)
                        Text("en cours").font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 11)
            .background(
                message.role == .user ? JarvisPalette.blue.opacity(0.82) : .white.opacity(0.055),
                in: RoundedRectangle(cornerRadius: 17)
            )
            .foregroundStyle(message.role == .user ? .white : .primary)
            if message.role != .user { Spacer(minLength: 100) }
        }
        .frame(maxWidth: .infinity)
    }

    private var attributedContent: AttributedString {
        (try? AttributedString(markdown: message.content)) ?? AttributedString(message.content)
    }
}
