import SwiftUI

struct MemoryView: View {
    @EnvironmentObject private var model: AppModel
    @State private var search = ""

    private var conversations: [ConversationSummary] {
        guard !search.isEmpty else { return model.snapshot.conversations }
        return model.snapshot.conversations.filter {
            $0.displayTitle.localizedCaseInsensitiveContains(search)
                || ($0.lastMessage?.localizedCaseInsensitiveContains(search) == true)
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                SectionHeader(
                    eyebrow: "CONTINUITÉ",
                    title: "Mémoire",
                    subtitle: "Reprendre une conversation sans repartir de zéro."
                )
                HStack {
                    Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                    TextField("Rechercher dans les conversations…", text: $search)
                        .textFieldStyle(.plain)
                }
                .padding(13)
                .jarvisGlass(cornerRadius: 15)

                HStack(spacing: 12) {
                    memoryMetric("\(model.snapshot.conversations.count)", "conversations", "bubble.left.and.text.bubble.right")
                    memoryMetric("\(model.snapshot.status?.agentsRegistered?.count ?? 0)", "agents spécialisés", "person.3.sequence.fill")
                    memoryMetric(model.snapshot.status?.models?.main ?? "—", "cerveau principal", "brain")
                }

                if conversations.isEmpty {
                    EmptyState(symbol: "brain.head.profile", title: "Aucun souvenir trouvé", subtitle: "Modifiez la recherche ou commencez une conversation.")
                        .frame(height: 300)
                        .jarvisGlass()
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 290), spacing: 14)], spacing: 14) {
                        ForEach(conversations) { conversation in
                            Button {
                                Task { await model.openConversation(conversation) }
                            } label: {
                                VStack(alignment: .leading, spacing: 11) {
                                    HStack {
                                        Image(systemName: conversation.pinned == 1 ? "pin.fill" : "bubble.left.fill")
                                            .foregroundStyle(JarvisPalette.cyan)
                                        Spacer()
                                        Text("\(conversation.msgCount ?? 0) msg")
                                            .font(.caption2).foregroundStyle(.tertiary)
                                    }
                                    Text(conversation.displayTitle).font(.headline).lineLimit(2)
                                    Text(conversation.lastMessage ?? "Aucun message")
                                        .font(.caption).foregroundStyle(.secondary).lineLimit(3)
                                    Spacer(minLength: 2)
                                    Text(relativeDate(conversation.lastMessageAt ?? conversation.startedAt))
                                        .font(.caption2).foregroundStyle(.tertiary)
                                }
                                .padding(16)
                                .frame(maxWidth: .infinity, minHeight: 155, alignment: .topLeading)
                                .jarvisGlass(cornerRadius: 18)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            .padding(28)
        }
        .navigationTitle("Mémoire")
    }

    private func memoryMetric(_ value: String, _ label: String, _ symbol: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: symbol).font(.title2).foregroundStyle(JarvisPalette.blue)
            VStack(alignment: .leading, spacing: 2) {
                Text(value).font(.headline).lineLimit(1)
                Text(label).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
        }
        .padding(15)
        .frame(maxWidth: .infinity)
        .jarvisGlass(cornerRadius: 17)
    }

    private func relativeDate(_ value: String?) -> String {
        guard let value, let date = value.jarvisDate else { return "Récemment" }
        return date.formatted(.relative(presentation: .named))
    }
}
